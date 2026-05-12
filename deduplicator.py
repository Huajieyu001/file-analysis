"""
去重核心编排
-----------
三段式逐步求精策略，逐层过滤缩小候选集：

  Pass 1 — 按文件大小分组，大小唯一的文件直接排除(覆盖绝大多数)
  Pass 2 — 同大小文件计算 quick_hash(首尾各64KB)，quick_hash 唯一的排除
  Pass 3 — 同 quick_hash 文件计算 full_hash(完整文件)，确认最终重复

快速模式(fast_mode=True)跳过 Pass 3，直接用 quick_hash 判定重复，
牺牲极小精度换取数量级的扫描速度提升。

所有哈希计算使用 ThreadPoolExecutor 并行执行，I/O 密集型任务收益显著。
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from scanner import walk_files
from hasher import quick_hash, full_hash
from config import WORKER_THREADS

# Pass 1 批量写入 DB 的记录数
PASS1_BATCH = 2000


def run_dedup(paths, db, force=False, progress_callback=None, extensions=None, fast_mode=False):
    """Main dedup orchestration.

    Pass 1: Walk files, record size/mtime. Skip unchanged files (unless force=True).
    Pass 2: For same-size groups, compute quick_hash in parallel.
    Pass 3: For same quick_hash groups, compute full_hash (skipped in fast_mode).
    """
    t0 = time.time()

    # Force mode: delete ALL records, then scan re-adds only what's on disk
    if force:
        db.conn.execute("DELETE FROM file_index")
        db.conn.commit()

    # Load existing paths (empty if force mode just deleted everything)
    existing = db.existing_paths_map()

    # ---- Pass 1: Walk and record file sizes ----
    if progress_callback:
        progress_callback("pass1_start", "Scanning files...")

    pass1_start = time.time()

    batch = []
    new_count = 0
    skipped_count = 0
    # Track which files from DB still exist on disk
    missing_set = set(existing.keys())

    for file_path, file_size, mtime_ns in walk_files(paths, extensions=extensions):
        missing_set.discard(file_path)

        prev = existing.get(file_path)
        # force=False: skip unchanged files. force=True: re-hash everything
        if prev and not force:
            prev_size, prev_mtime, prev_status = prev
            if prev_size == file_size and prev_mtime == mtime_ns:
                skipped_count += 1
                continue

        batch.append((file_path, file_size, mtime_ns))
        if len(batch) >= PASS1_BATCH:
            _flush_pass1(batch, db)
            new_count += len(batch)
            batch.clear()
            if progress_callback:
                progress_callback("pass1_progress", f"Scanned {new_count + skipped_count} files...")

    if batch:
        _flush_pass1(batch, db)
        new_count += len(batch)

    # Mark files that no longer exist
    for path in missing_set:
        db.mark_missing(path)

    pass1_time = time.time() - pass1_start
    if progress_callback:
        progress_callback(
            "pass1_done",
            f"Pass 1 done: {new_count} new/changed, {skipped_count} unchanged, "
            f"{len(missing_set)} removed ({pass1_time:.1f}s)",
        )

    # ---- Pass 2: Quick hash for same-size groups ----
    pass2_start = time.time()
    if progress_callback:
        progress_callback("pass2_start", "Computing quick hashes...")

    size_groups = db.get_size_groups(min_group_size=2)
    qhash_queue = []
    for file_size, _ in size_groups:
        files = db.get_files_by_size(file_size)
        qhash_queue.extend(files)

    if qhash_queue:
        _parallel_hash(
            qhash_queue,
            db,
            hash_func=quick_hash,
            update_func=db.update_quick_hash,
            skip_func=db.update_skipped,
            description="quick hash",
            progress_callback=progress_callback,
            pass_label="pass2",
            progress_interval=500,
        )
        db.conn.commit()
        try:
            db.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass

    # After quick_hash, mark unique qhash files as done
    qhash_groups = db.get_qhash_groups(min_group_size=2)
    qhash_set = set()
    for qh, fsz, _ in qhash_groups:
        qhash_set.add((qh, fsz))
    # Files with same size but unique qhash don't need full_hash
    _mark_unique_qhash_as_done(db, size_groups, qhash_set)

    if progress_callback:
        progress_callback(
            "pass2_done",
            f"Pass 2 done: {len(qhash_queue)} files quick-hashed, "
            f"{len(qhash_groups)} groups need full hash",
        )

    # ---- Pass 3: Full hash (skipped in fast_mode) ----
    if fast_mode:
        # Use quick_hash as final hash: same (qhash, size) => duplicate
        if progress_callback:
            progress_callback("pass3_start", "Fast mode: using quick hash as final, skipping full hash...")
        now = int(time.time())
        for qh, fsz, _ in qhash_groups:
            db.conn.execute(
                "UPDATE file_index SET full_hash=?, status='full_hashed', scan_time=? "
                "WHERE quick_hash=? AND file_size=?",
                (qh, now, qh, fsz),
            )
        db.conn.commit()
        if progress_callback:
            progress_callback("pass3_done",
                f"Fast mode: {len(qhash_groups)} duplicate groups confirmed via quick hash")
    else:
        if progress_callback:
            progress_callback("pass3_start", "Computing full hashes...")

        full_queue = []
        for quick_hash_val, file_size, _ in qhash_groups:
            files = db.get_files_by_qhash_and_size(quick_hash_val, file_size)
            full_queue.extend(files)

        if full_queue:
            _parallel_hash(
                full_queue,
                db,
                hash_func=full_hash,
                update_func=db.update_full_hash,
                skip_func=db.update_skipped,
                description="full hash",
                progress_callback=progress_callback,
                pass_label="pass3",
                progress_interval=50,
            )
            db.conn.commit()
            try:
                db.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except Exception:
                pass

    total_time = time.time() - t0
    pass2_time = time.time() - pass2_start
    if progress_callback:
        progress_callback(
            "done",
            f"Scan complete in {total_time:.1f}s. "
            f"Total files: {new_count + skipped_count}, duplicates found.",
        )
        progress_callback("timing",
            f"总耗时 {total_time:.1f}秒 | 快速Hash耗时 {pass2_time:.1f}秒 | 扫描文件耗时 {pass1_time:.1f}秒")

    return db.get_duplicate_groups()


def _flush_pass1(batch, db):
    """Commit a batch of Pass 1 records."""
    for file_path, file_size, mtime_ns in batch:
        db.upsert_file(file_path, file_size, mtime_ns, status="sized")
    db.conn.commit()
    # Periodic WAL checkpoint to prevent unbounded WAL growth
    if len(batch) >= PASS1_BATCH:
        try:
            db.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass


def _parallel_hash(
    file_list,
    db,
    hash_func,
    update_func,
    skip_func,
    description,
    progress_callback=None,
    pass_label="",
    progress_interval=500,
):
    """Process a list of files with a hash function in parallel."""
    total = len(file_list)
    done = 0

    with ThreadPoolExecutor(max_workers=WORKER_THREADS) as executor:
        futures = {
            executor.submit(hash_func, file_path): (file_path, file_size)
            for file_path, file_size in file_list
        }

        for future in as_completed(futures):
            file_path, file_size = futures[future]
            done += 1
            try:
                result = future.result()
                if result is not None:
                    update_func(file_path, result)
                else:
                    skip_func(file_path)
            except Exception:
                skip_func(file_path)

            if done % progress_interval == 0 and progress_callback:
                progress_callback(
                    f"{pass_label}_progress",
                    f"{description}: {done}/{total}",
                )


def _mark_unique_qhash_as_done(db, size_groups, keep_qhash_set):
    """For files whose (qhash, size) is unique among their size group, mark as full_hashed
    with qhash as their proxy — no need for full hash since they have no collision."""
    for file_size, _ in size_groups:
        # Find qhashes for this size that are NOT in keep set
        rows = db.conn.execute(
            "SELECT file_path, quick_hash FROM file_index "
            "WHERE file_size = ? AND status = 'quick_hashed' "
            "GROUP BY quick_hash HAVING COUNT(*) = 1",
            (file_size,),
        ).fetchall()
        now = int(time.time())
        for file_path, qh in rows:
            if (qh, file_size) not in keep_qhash_set:
                db.conn.execute(
                    "UPDATE file_index SET full_hash=?, status='full_hashed', scan_time=? WHERE file_path=?",
                    (qh, now, file_path),
                )
    db.conn.commit()
