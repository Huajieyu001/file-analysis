import sqlite3
import time
from config import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS file_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT UNIQUE NOT NULL,
    file_size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    quick_hash BLOB,
    full_hash BLOB,
    scan_time INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'new'
);

CREATE INDEX IF NOT EXISTS idx_size ON file_index(file_size);
CREATE INDEX IF NOT EXISTS idx_qhash_size ON file_index(quick_hash, file_size);
CREATE INDEX IF NOT EXISTS idx_full_hash ON file_index(full_hash);
CREATE INDEX IF NOT EXISTS idx_status ON file_index(status);

CREATE TABLE IF NOT EXISTS scan_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class Database:
    def __init__(self, db_path=None, timeout=30):
        self.db_path = db_path or DB_PATH
        self._timeout = timeout
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=self._timeout)
            self._conn.execute("PRAGMA busy_timeout=30000")  # 30s wait before giving up
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        return self._conn

    def init_db(self):
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ---- File index operations ----

    def get_file_record(self, file_path):
        """Return (id, file_path, file_size, mtime_ns, quick_hash, full_hash, scan_time, status) or None."""
        row = self.conn.execute(
            "SELECT id, file_path, file_size, mtime_ns, quick_hash, full_hash, scan_time, status "
            "FROM file_index WHERE file_path = ?",
            (file_path,),
        ).fetchone()
        return row

    def upsert_file(self, file_path, file_size, mtime_ns, status="sized"):
        """Insert or update a file record. Returns the row id."""
        now = int(time.time())
        self.conn.execute(
            """INSERT INTO file_index (file_path, file_size, mtime_ns, scan_time, status)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(file_path) DO UPDATE SET
               file_size=excluded.file_size,
               mtime_ns=excluded.mtime_ns,
               quick_hash=NULL,
               full_hash=NULL,
               scan_time=excluded.scan_time,
               status=excluded.status""",
            (file_path, file_size, mtime_ns, now, status),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM file_index WHERE file_path = ?", (file_path,)
        ).fetchone()
        return row[0]

    def update_quick_hash(self, file_path, quick_hash):
        self.conn.execute(
            "UPDATE file_index SET quick_hash=?, status='quick_hashed', scan_time=? WHERE file_path=?",
            (quick_hash, int(time.time()), file_path),
        )

    def update_full_hash(self, file_path, full_hash):
        self.conn.execute(
            "UPDATE file_index SET full_hash=?, status='full_hashed', scan_time=? WHERE file_path=?",
            (full_hash, int(time.time()), file_path),
        )

    def update_skipped(self, file_path):
        self.conn.execute(
            "UPDATE file_index SET status='skipped', scan_time=? WHERE file_path=?",
            (int(time.time()), file_path),
        )

    def mark_missing(self, file_path):
        """Mark a file as missing (no longer exists on disk)."""
        self.conn.execute(
            "UPDATE file_index SET status='missing', scan_time=? WHERE file_path=?",
            (int(time.time()), file_path),
        )

    def mark_all_missing(self):
        """Mark all non-missing files as missing (for detecting removed files)."""
        self.conn.execute(
            "UPDATE file_index SET status='missing' WHERE status != 'missing'"
        )

    def remove_missing(self):
        """Delete all records with status='missing'."""
        self.conn.execute("DELETE FROM file_index WHERE status = 'missing'")
        self.conn.commit()

    def get_files_by_status(self, status, limit=10000):
        """Batch fetch files by status."""
        rows = self.conn.execute(
            "SELECT id, file_path, file_size, mtime_ns, quick_hash, full_hash, scan_time, status "
            "FROM file_index WHERE status = ? LIMIT ?",
            (status, limit),
        ).fetchall()
        return rows

    def get_size_groups(self, min_group_size=2):
        """Return (file_size, count) for sizes with count >= min_group_size."""
        rows = self.conn.execute(
            "SELECT file_size, COUNT(*) as cnt FROM file_index "
            "WHERE status = 'sized' AND file_size >= ? "
            "GROUP BY file_size HAVING cnt >= ? "
            "ORDER BY cnt DESC",
            (1, min_group_size),
        ).fetchall()
        return rows

    def get_files_by_size(self, file_size, limit=5000):
        """Get files of a specific size that need quick_hash."""
        rows = self.conn.execute(
            "SELECT file_path, file_size FROM file_index "
            "WHERE file_size = ? AND status = 'sized' "
            "LIMIT ?",
            (file_size, limit),
        ).fetchall()
        return rows

    def get_qhash_groups(self, min_group_size=2):
        """Return (quick_hash, file_size, count) groups where count >= min_group_size."""
        rows = self.conn.execute(
            "SELECT quick_hash, file_size, COUNT(*) as cnt FROM file_index "
            "WHERE status = 'quick_hashed' AND quick_hash IS NOT NULL "
            "GROUP BY quick_hash, file_size HAVING cnt >= ? "
            "ORDER BY cnt DESC",
            (min_group_size,),
        ).fetchall()
        return rows

    def get_files_by_qhash_and_size(self, quick_hash, file_size):
        """Get files with given quick_hash and size that need full_hash."""
        rows = self.conn.execute(
            "SELECT file_path, file_size FROM file_index "
            "WHERE quick_hash = ? AND file_size = ? AND status = 'quick_hashed'",
            (quick_hash, file_size),
        ).fetchall()
        return rows

    def get_qhash_unique_count(self, file_size):
        """Count how many files of given size have unique quick_hash (already excluded)."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT quick_hash FROM file_index "
            "  WHERE file_size = ? AND status = 'quick_hashed' "
            "  GROUP BY quick_hash HAVING COUNT(*) = 1"
            ")",
            (file_size,),
        ).fetchone()
        return row[0] if row else 0

    def get_duplicate_groups(self):
        """Return duplicate groups: [(full_hash, file_size, [(file_path, mtime_ns), ...]), ...]"""
        # Find full_hashes that appear more than once
        dup_hashes = self.conn.execute(
            "SELECT full_hash FROM file_index "
            "WHERE status = 'full_hashed' AND full_hash IS NOT NULL "
            "GROUP BY full_hash HAVING COUNT(*) > 1"
        ).fetchall()

        groups = []
        for (full_hash,) in dup_hashes:
            files = self.conn.execute(
                "SELECT file_path, file_size, mtime_ns FROM file_index WHERE full_hash = ?",
                (full_hash,),
            ).fetchall()
            groups.append((full_hash, files[0][1], files))
        return groups

    def get_stats(self):
        """Return a dict of statistics."""
        total = self.conn.execute(
            "SELECT COUNT(*) FROM file_index WHERE status != 'missing'"
        ).fetchone()[0]
        sized = self.conn.execute(
            "SELECT COUNT(*) FROM file_index WHERE status = 'sized'"
        ).fetchone()[0]
        qh = self.conn.execute(
            "SELECT COUNT(*) FROM file_index WHERE status = 'quick_hashed'"
        ).fetchone()[0]
        fh = self.conn.execute(
            "SELECT COUNT(*) FROM file_index WHERE status = 'full_hashed'"
        ).fetchone()[0]
        skipped = self.conn.execute(
            "SELECT COUNT(*) FROM file_index WHERE status = 'skipped'"
        ).fetchone()[0]
        missing = self.conn.execute(
            "SELECT COUNT(*) FROM file_index WHERE status = 'missing'"
        ).fetchone()[0]

        dup_groups = self.conn.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT full_hash FROM file_index "
            "  WHERE status = 'full_hashed' AND full_hash IS NOT NULL "
            "  GROUP BY full_hash HAVING COUNT(*) > 1"
            ")"
        ).fetchone()[0]

        wasted_bytes = self.conn.execute(
            "SELECT COALESCE(SUM(wasted), 0) FROM ("
            "  SELECT (COUNT(*) - 1) * file_size as wasted FROM file_index "
            "  WHERE status = 'full_hashed' AND full_hash IS NOT NULL "
            "  GROUP BY full_hash HAVING COUNT(*) > 1"
            ")"
        ).fetchone()[0]

        # Total unique file size
        unique_size = self.conn.execute(
            "SELECT COALESCE(SUM(file_size), 0) FROM ("
            "  SELECT file_size FROM file_index "
            "  WHERE status = 'full_hashed' AND full_hash IS NOT NULL "
            "  GROUP BY full_hash"
            ")"
        ).fetchone()[0]

        return {
            "total_files": total,
            "sized": sized,
            "quick_hashed": qh,
            "full_hashed": fh,
            "skipped": skipped,
            "missing": missing,
            "duplicate_groups": dup_groups,
            "wasted_bytes": wasted_bytes,
            "unique_size": unique_size,
        }

    def total_files_need_processing(self):
        """Count files that still need hashing."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM file_index WHERE status IN ('sized', 'quick_hashed')"
        ).fetchone()
        return row[0]

    def existing_paths_map(self):
        """Return dict of {file_path: (file_size, mtime_ns, status)} for all non-missing files."""
        rows = self.conn.execute(
            "SELECT file_path, file_size, mtime_ns, status FROM file_index WHERE status != 'missing'"
        ).fetchall()
        return {r[0]: (r[1], r[2], r[3]) for r in rows}
