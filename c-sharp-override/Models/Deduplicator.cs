using System.Collections.Concurrent;

namespace CleanDup.Models;

/// <summary>
/// Core dedup orchestration — equivalent to Python deduplicator.py.
/// Three-pass strategy: size → quick_hash → full_hash.
/// </summary>
public class Deduplicator
{
    public event Action<string, string>? Progress;

    /// <summary>Run the three-pass dedup scan.</summary>
    public List<DupGroup> RunDedup(List<string> paths, Database db, bool force = false,
        HashSet<string>? extensions = null, bool fastMode = true)
    {
        Emit("pass1_start", "Scanning files...");

        // ── Pass 1: Walk and record file sizes ──
        var existing = force ? new() : db.ExistingPathsMap();
        var missingSet = force ? new() : new HashSet<string>(existing.Keys);

        var batch = new List<(string path, long size, long mtime)>();
        int newCount = 0, skippedCount = 0;

        foreach (var entry in Scanner.WalkFiles(paths, extensions))
        {
            missingSet.Remove(entry.Path);
            if (!force && existing.TryGetValue(entry.Path, out var prev))
            {
                if (prev.size == entry.Size && prev.mtime == entry.MtimeNs)
                {
                    skippedCount++;
                    continue;
                }
            }
            batch.Add((entry.Path, entry.Size, entry.MtimeNs));
            if (batch.Count >= 2000) { FlushPass1(batch, db); newCount += batch.Count; batch.Clear(); }
        }
        if (batch.Count > 0) { FlushPass1(batch, db); newCount += batch.Count; }

        foreach (var p in missingSet) db.UpdateSkipped(p); // mark as missing

        Emit("pass1_done", $"Pass 1 done: {newCount} new/changed, {skippedCount} unchanged, {missingSet.Count} removed");

        // ── Pass 2: Quick hash for same-size groups ──
        Emit("pass2_start", "Computing quick hashes...");
        var sizeGroups = db.GetSizeGroups(2);
        var qhashQueue = new List<(string path, long size)>();
        foreach (var (size, _) in sizeGroups)
            qhashQueue.AddRange(db.GetFilesBySize(size));

        if (qhashQueue.Count > 0)
            ParallelHash(qhashQueue, db, Hasher.QuickHash, db.UpdateQuickHash, "quick hash", 500);

        var qhashGroups = db.GetQHashGroups(2);
        var keepSet = new HashSet<(byte[], long)>(new QHashComparer());
        foreach (var (qh, sz, _) in qhashGroups) keepSet.Add((qh, sz));
        db.MarkUniqueQHashAsDone(sizeGroups, keepSet);

        Emit("pass2_done", $"Pass 2 done: {qhashQueue.Count} files, {qhashGroups.Count} groups need full hash");

        // ── Pass 3: Full hash (skipped in fast mode) ──
        if (fastMode)
        {
            Emit("pass3_start", "Fast mode: using quick hash as final...");
            var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            foreach (var (qh, sz, _) in qhashGroups)
            {
                var files = db.GetFilesByQHashAndSize(qh, sz);
                foreach (var (fp, _) in files)
                    db.UpdateFullHash(fp, qh);
            }
            Emit("pass3_done", $"Fast mode: {qhashGroups.Count} groups confirmed via quick hash");
        }
        else
        {
            Emit("pass3_start", "Computing full hashes...");
            var fullQueue = new List<(string path, long size)>();
            foreach (var (qh, sz, _) in qhashGroups)
                fullQueue.AddRange(db.GetFilesByQHashAndSize(qh, sz));

            if (fullQueue.Count > 0)
                ParallelHash(fullQueue, db, Hasher.FullHash, db.UpdateFullHash, "full hash", 50);
        }

        Emit("done", "Scan complete");
        return db.GetDuplicateGroups();
    }

    private static void FlushPass1(List<(string path, long size, long mtime)> batch, Database db)
    {
        foreach (var (p, s, m) in batch) db.UpsertFile(p, s, m, "sized");
    }

    private void ParallelHash(List<(string path, long size)> fileList, Database db,
        Func<string, byte[]?> hashFunc, Action<string, byte[]> updateFunc,
        string description, int progressInterval)
    {
        int total = fileList.Count, done = 0;
        var options = new ParallelOptions { MaxDegreeOfParallelism = Config.WorkerThreads };
        Parallel.ForEach(fileList, options, (item) =>
        {
            var result = hashFunc(item.path);
            lock (db)
            {
                if (result != null) updateFunc(item.path, result);
                else db.UpdateSkipped(item.path);
            }
            var d = Interlocked.Increment(ref done);
            if (d % progressInterval == 0)
                Emit($"{description}_progress", $"{description}: {d}/{total}");
        });
    }

    private void Emit(string stage, string msg) => Progress?.Invoke(stage, msg);
}

internal class QHashComparer : IEqualityComparer<(byte[] hash, long size)>
{
    public bool Equals((byte[] hash, long size) x, (byte[] hash, long size) y) =>
        x.size == y.size && x.hash.AsSpan().SequenceEqual(y.hash);
    public int GetHashCode((byte[] hash, long size) obj) => obj.hash[0] ^ obj.hash.Length ^ obj.size.GetHashCode();
}
