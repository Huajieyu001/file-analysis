namespace CleanDup.Models;

/// <summary>
/// Global configuration constants, equivalent to Python config.py
/// </summary>
public static class Config
{
    // Database path — stored next to the exe
    public static string DbPath =>
        Path.Combine(AppContext.BaseDirectory, "dedup.db");

    // Quick hash: read head + tail bytes for fast filtering
    public const int QuickHashHeadBytes = 64 * 1024;  // 64KB
    public const int QuickHashTailBytes = 64 * 1024;
    public const int HashReadChunk = 1024 * 1024;      // 1MB chunks

    // I/O thread count for hash computation
    public const int WorkerThreads = 4;

    // Directories to skip during file walk
    public static readonly HashSet<string> SkipDirs = new()
    {
        "$RECYCLE.BIN", "System Volume Information",
        "Windows", "Program Files", "Program Files (x86)",
        "ProgramData", "Recovery", ".git", "__pycache__", "node_modules"
    };

    // Min file size in MB (persisted to settings.json)
    public static int MinFileSizeMb { get; set; } = 200;
    public static long MinFileSizeBytes => MinFileSizeMb * 1024L * 1024L;

    // System files always skipped
    public static readonly HashSet<string> SkipExtensions = new()
        { ".DS_Store", ".Thumbs.db", ".thumb", ".ini" };

    // Settings file path
    public static string SettingsPath =>
        Path.Combine(AppContext.BaseDirectory, "settings.json");
}
