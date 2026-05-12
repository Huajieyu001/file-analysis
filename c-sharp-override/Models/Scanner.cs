namespace CleanDup.Models;

/// <summary>
/// File system walker — enumerates files with filtering, equivalent to Python scanner.py.
/// Yields (path, size, mtime_ns) tuples.
/// </summary>
public static class Scanner
{
    public static IEnumerable<FileEntry> WalkFiles(IEnumerable<string> paths, HashSet<string>? extensions = null)
    {
        foreach (var basePath in paths)
        {
            var resolved = Path.GetFullPath(basePath);
            if (File.Exists(resolved))
            {
                var fi = SafeFileInfo(resolved);
                if (fi != null && ShouldInclude(fi, extensions))
                    yield return new FileEntry(fi.FullName, fi.Length, ToNano(fi.LastWriteTimeUtc));
                continue;
            }
            if (!Directory.Exists(resolved)) continue;

            foreach (var entry in WalkDir(resolved, extensions))
                yield return entry;
        }
    }

    private static IEnumerable<FileEntry> WalkDir(string root, HashSet<string>? extensions)
    {
        var dirs = new Stack<string>();
        dirs.Push(root);
        while (dirs.Count > 0)
        {
            var dir = dirs.Pop();
            string[] subDirs, files;
            try { subDirs = Directory.GetDirectories(dir); files = Directory.GetFiles(dir); }
            catch { continue; }

            foreach (var f in files)
            {
                FileInfo? fi = null;
                try { fi = new FileInfo(f); } catch { continue; }
                if (fi.Length < Config.MinFileSizeBytes) continue;
                var ext = Path.GetExtension(f).ToLowerInvariant();
                if (Config.SkipExtensions.Contains(ext)) continue;
                if (extensions != null && extensions.Count > 0 && !extensions.Contains(ext)) continue;
                yield return new FileEntry(fi.FullName, fi.Length, ToNano(fi.LastWriteTimeUtc));
            }

            foreach (var d in subDirs)
            {
                var dname = Path.GetFileName(d);
                if (Config.SkipDirs.Contains(dname) || dname.StartsWith('.')) continue;
                dirs.Push(d);
            }
        }
    }

    private static FileInfo? SafeFileInfo(string path)
    {
        try { return new FileInfo(path); } catch { return null; }
    }

    private static bool ShouldInclude(FileInfo fi, HashSet<string>? extensions)
    {
        if (fi.Length < Config.MinFileSizeBytes) return false;
        var ext = Path.GetExtension(fi.Name).ToLowerInvariant();
        if (Config.SkipExtensions.Contains(ext)) return false;
        if (extensions != null && extensions.Count > 0 && !extensions.Contains(ext)) return false;
        return true;
    }

    // Windows NT epoch: 1601-01-01; Unix epoch: 1970-01-01; difference in 100ns ticks
    private static long ToNano(DateTime dt) => dt.ToFileTimeUtc() * 100;
}

public record FileEntry(string Path, long Size, long MtimeNs);
