using Microsoft.Data.Sqlite;

namespace CleanDup.Models;

/// <summary>
/// SQLite database layer — equivalent to Python database.py.
/// WAL mode, indexed queries, incremental scan support via mtime_ns.
/// </summary>
public class Database : IDisposable
{
    private readonly SqliteConnection _conn;

    public Database(string? path = null)
    {
        _conn = new SqliteConnection($"Data Source={path ?? Config.DbPath}");
        _conn.Open();
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=30000; PRAGMA cache_size=-64000;";
        cmd.ExecuteNonQuery();
    }

    public void InitDb()
    {
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = @"
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
        ";
        cmd.ExecuteNonQuery();
    }

    /// <summary>Returns file record or null.</summary>
    public FileRecord? GetFileRecord(string filePath)
    {
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = "SELECT id, file_path, file_size, mtime_ns, quick_hash, full_hash, scan_time, status FROM file_index WHERE file_path = @p";
        cmd.Parameters.AddWithValue("@p", filePath);
        using var r = cmd.ExecuteReader();
        return r.Read() ? FileRecord.FromReader(r) : null;
    }

    /// <summary>Insert or update a file record. Resets hashes on update.</summary>
    public long UpsertFile(string filePath, long fileSize, long mtimeNs, string status = "sized")
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = @"INSERT INTO file_index (file_path, file_size, mtime_ns, scan_time, status)
            VALUES (@p, @s, @m, @t, @st)
            ON CONFLICT(file_path) DO UPDATE SET
            file_size=excluded.file_size, mtime_ns=excluded.mtime_ns,
            quick_hash=NULL, full_hash=NULL,
            scan_time=excluded.scan_time, status=excluded.status;
            SELECT id FROM file_index WHERE file_path=@p;";
        cmd.Parameters.AddWithValue("@p", filePath);
        cmd.Parameters.AddWithValue("@s", fileSize);
        cmd.Parameters.AddWithValue("@m", mtimeNs);
        cmd.Parameters.AddWithValue("@t", now);
        cmd.Parameters.AddWithValue("@st", status);
        return (long)cmd.ExecuteScalar()!;
    }

    public void UpdateQuickHash(string path, byte[] hash)
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = "UPDATE file_index SET quick_hash=@h, status='quick_hashed', scan_time=@t WHERE file_path=@p";
        cmd.Parameters.AddWithValue("@h", hash);
        cmd.Parameters.AddWithValue("@t", now);
        cmd.Parameters.AddWithValue("@p", path);
        cmd.ExecuteNonQuery();
    }

    public void UpdateFullHash(string path, byte[] hash)
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = "UPDATE file_index SET full_hash=@h, status='full_hashed', scan_time=@t WHERE file_path=@p";
        cmd.Parameters.AddWithValue("@h", hash);
        cmd.Parameters.AddWithValue("@t", now);
        cmd.Parameters.AddWithValue("@p", path);
        cmd.ExecuteNonQuery();
    }

    public void UpdateSkipped(string path)
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = "UPDATE file_index SET status='skipped', scan_time=@t WHERE file_path=@p";
        cmd.Parameters.AddWithValue("@t", now);
        cmd.Parameters.AddWithValue("@p", path);
        cmd.ExecuteNonQuery();
    }

    /// <summary>All non-missing files as dict for incremental detection.</summary>
    public Dictionary<string, (long size, long mtime, string status)> ExistingPathsMap()
    {
        var map = new Dictionary<string, (long, long, string)>();
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = "SELECT file_path, file_size, mtime_ns, status FROM file_index WHERE status != 'missing'";
        using var r = cmd.ExecuteReader();
        while (r.Read())
            map[r.GetString(0)] = (r.GetInt64(1), r.GetInt64(2), r.GetString(3));
        return map;
    }

    /// <summary>Size groups with ≥ minGroupSize files.</summary>
    public List<(long size, int count)> GetSizeGroups(int minGroupSize = 2)
    {
        var list = new List<(long, int)>();
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = @"SELECT file_size, COUNT(*) FROM file_index
            WHERE status='sized' AND file_size>=1
            GROUP BY file_size HAVING COUNT(*)>=@n ORDER BY COUNT(*) DESC";
        cmd.Parameters.AddWithValue("@n", minGroupSize);
        using var r = cmd.ExecuteReader();
        while (r.Read()) list.Add((r.GetInt64(0), r.GetInt32(1)));
        return list;
    }

    /// <summary>Files of a specific size needing quick_hash.</summary>
    public List<(string path, long size)> GetFilesBySize(long fileSize, int limit = 5000)
    {
        var list = new List<(string, long)>();
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = "SELECT file_path, file_size FROM file_index WHERE file_size=@s AND status='sized' LIMIT @l";
        cmd.Parameters.AddWithValue("@s", fileSize);
        cmd.Parameters.AddWithValue("@l", limit);
        using var r = cmd.ExecuteReader();
        while (r.Read()) list.Add((r.GetString(0), r.GetInt64(1)));
        return list;
    }

    /// <summary>Quick-hash groups with ≥ minGroupSize.</summary>
    public List<(byte[] qhash, long size, int count)> GetQHashGroups(int minGroupSize = 2)
    {
        var list = new List<(byte[], long, int)>();
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = @"SELECT quick_hash, file_size, COUNT(*) FROM file_index
            WHERE status='quick_hashed' AND quick_hash IS NOT NULL
            GROUP BY quick_hash, file_size HAVING COUNT(*)>=@n ORDER BY COUNT(*) DESC";
        cmd.Parameters.AddWithValue("@n", minGroupSize);
        using var r = cmd.ExecuteReader();
        while (r.Read()) list.Add(((byte[])r[0], r.GetInt64(1), r.GetInt32(2)));
        return list;
    }

    /// <summary>Files matching a specific quick_hash+size needing full_hash.</summary>
    public List<(string path, long size)> GetFilesByQHashAndSize(byte[] qhash, long size)
    {
        var list = new List<(string, long)>();
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = "SELECT file_path, file_size FROM file_index WHERE quick_hash=@h AND file_size=@s AND status='quick_hashed'";
        cmd.Parameters.AddWithValue("@h", qhash);
        cmd.Parameters.AddWithValue("@s", size);
        using var r = cmd.ExecuteReader();
        while (r.Read()) list.Add((r.GetString(0), r.GetInt64(1)));
        return list;
    }

    /// <summary>Mark unique-qhash-as-group files as done.</summary>
    public void MarkUniqueQHashAsDone(List<(long size, int count)> sizeGroups, HashSet<(byte[], long)> keepSet)
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        foreach (var (fileSize, _) in sizeGroups)
        {
            using var cmd = _conn.CreateCommand();
            cmd.CommandText = @"UPDATE file_index SET full_hash=quick_hash, status='full_hashed', scan_time=@t
                WHERE file_size=@s AND status='quick_hashed'
                AND quick_hash IN (SELECT quick_hash FROM file_index WHERE file_size=@s2 AND status='quick_hashed' GROUP BY quick_hash HAVING COUNT(*)=1)";
            cmd.Parameters.AddWithValue("@t", now);
            cmd.Parameters.AddWithValue("@s", fileSize);
            cmd.Parameters.AddWithValue("@s2", fileSize);
            cmd.ExecuteNonQuery();
        }
    }

    /// <summary>Return all duplicate groups.</summary>
    public List<DupGroup> GetDuplicateGroups()
    {
        var groups = new List<DupGroup>();
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = @"SELECT full_hash FROM file_index
            WHERE status='full_hashed' AND full_hash IS NOT NULL
            GROUP BY full_hash HAVING COUNT(*)>1";
        using var r = cmd.ExecuteReader();
        var hashes = new List<byte[]>();
        while (r.Read()) hashes.Add((byte[])r[0]);

        foreach (var h in hashes)
        {
            using var c2 = _conn.CreateCommand();
            c2.CommandText = "SELECT file_path, file_size, mtime_ns FROM file_index WHERE full_hash=@h";
            c2.Parameters.AddWithValue("@h", h);
            using var r2 = c2.ExecuteReader();
            var files = new List<(string path, long size, long mtime)>();
            while (r2.Read()) files.Add((r2.GetString(0), r2.GetInt64(1), r2.GetInt64(2)));
            files.Sort((a, b) => a.mtime.CompareTo(b.mtime)); // oldest first
            groups.Add(new DupGroup(h, files[0].size, files));
        }
        return groups;
    }

    /// <summary>Statistics summary.</summary>
    public DbStats GetStats()
    {
        var s = new DbStats();
        using var cmd = _conn.CreateCommand();
        s.TotalFiles = ExecuteScalarLong("SELECT COUNT(*) FROM file_index WHERE status!='missing'");
        s.FullHashed = ExecuteScalarLong("SELECT COUNT(*) FROM file_index WHERE status='full_hashed'");
        s.Sized = ExecuteScalarLong("SELECT COUNT(*) FROM file_index WHERE status='sized'");
        s.QuickHashed = ExecuteScalarLong("SELECT COUNT(*) FROM file_index WHERE status='quick_hashed'");
        s.Skipped = ExecuteScalarLong("SELECT COUNT(*) FROM file_index WHERE status='skipped'");
        s.Missing = ExecuteScalarLong("SELECT COUNT(*) FROM file_index WHERE status='missing'");
        s.DuplicateGroups = ExecuteScalarLong(
            "SELECT COUNT(*) FROM (SELECT full_hash FROM file_index WHERE status='full_hashed' AND full_hash IS NOT NULL GROUP BY full_hash HAVING COUNT(*)>1)");
        s.WastedBytes = ExecuteScalarLong(
            "SELECT COALESCE(SUM(wasted),0) FROM (SELECT (COUNT(*)-1)*file_size as wasted FROM file_index WHERE status='full_hashed' AND full_hash IS NOT NULL GROUP BY full_hash HAVING COUNT(*)>1)");
        s.UniqueSize = ExecuteScalarLong(
            "SELECT COALESCE(SUM(file_size),0) FROM (SELECT file_size FROM file_index WHERE status='full_hashed' AND full_hash IS NOT NULL GROUP BY full_hash)");
        return s;
    }

    /// <summary>Search files by keyword.</summary>
    public List<SearchResult> SearchFiles(string keyword, int limit = 200)
    {
        var results = new List<SearchResult>();
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = @"SELECT f.file_path, f.file_size, f.mtime_ns, f.full_hash, f.status,
            CASE WHEN f.full_hash IS NOT NULL THEN
              (SELECT COUNT(*)-1 FROM file_index f2 WHERE f2.full_hash=f.full_hash)
            ELSE 0 END
            FROM file_index f WHERE f.file_path LIKE @k AND f.status!='missing'
            ORDER BY f.file_size DESC LIMIT @l";
        cmd.Parameters.AddWithValue("@k", $"%{keyword}%");
        cmd.Parameters.AddWithValue("@l", limit);
        using var r = cmd.ExecuteReader();
        while (r.Read())
            results.Add(new SearchResult(r.GetString(0), r.GetInt64(1), r.GetInt64(2),
                r.IsDBNull(3) ? null : (byte[])r[3], r.GetString(4), r.GetInt32(5)));
        return results;
    }

    /// <summary>Remove records where the file no longer exists on disk.</summary>
    public int RemoveNonexistent()
    {
        var gone = new List<long>();
        using (var cmd = _conn.CreateCommand())
        {
            cmd.CommandText = "SELECT id, file_path FROM file_index WHERE status!='missing'";
            using var r = cmd.ExecuteReader();
            while (r.Read())
                if (!File.Exists(r.GetString(1)))
                    gone.Add(r.GetInt64(0));
        }
        if (gone.Count == 0) return 0;
        using (var cmd = _conn.CreateCommand())
        {
            // Batch delete
            for (int i = 0; i < gone.Count; i += 500)
            {
                var chunk = gone.Skip(i).Take(500).ToList();
                cmd.CommandText = $"DELETE FROM file_index WHERE id IN ({string.Join(",", chunk)})";
                cmd.ExecuteNonQuery();
            }
        }
        return gone.Count;
    }

    public void Dispose() => _conn?.Dispose();

    private long ExecuteScalarLong(string sql)
    {
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = sql;
        var result = cmd.ExecuteScalar();
        return result is DBNull or null ? 0 : Convert.ToInt64(result);
    }
}

// ── Data types ──

public record FileRecord(long Id, string Path, long Size, long MtimeNs, byte[]? QuickHash, byte[]? FullHash, long ScanTime, string Status)
{
    public static FileRecord FromReader(SqliteDataReader r) => new(
        r.GetInt64(0), r.GetString(1), r.GetInt64(2), r.GetInt64(3),
        r.IsDBNull(4) ? null : (byte[])r[4], r.IsDBNull(5) ? null : (byte[])r[5],
        r.GetInt64(6), r.GetString(7));
}

public record DupGroup(byte[] Hash, long Size, List<(string path, long size, long mtime)> Files);

public record SearchResult(string Path, long Size, long MtimeNs, byte[]? Hash, string Status, int DupCount);

public class DbStats
{
    public long TotalFiles, Sized, QuickHashed, FullHashed, Skipped, Missing;
    public long DuplicateGroups, WastedBytes, UniqueSize;
}
