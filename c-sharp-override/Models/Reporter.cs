namespace CleanDup.Models;

/// <summary>
/// Human-readable size formatting and CSV/JSON export helpers.
/// </summary>
public static class Reporter
{
    private static readonly string[] SizeUnits = ["B", "KB", "MB", "GB", "TB"];

    public static string FormatSize(long bytes, int prec = 2)
    {
        if (bytes == 0) return $"0.{new string('0', prec)} B";
        var fsize = (double)bytes;
        var i = 0;
        while (fsize >= 1024 && i < SizeUnits.Length - 1)
        {
            fsize /= 1024;
            i++;
        }
        return $"{fsize:F${prec}} {SizeUnits[i]}";
    }
}
