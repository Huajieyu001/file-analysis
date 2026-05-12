using K4os.Hash.xxHash;

namespace CleanDup.Models;

/// <summary>
/// Hash computation using XXH128 (K4os.Hash.xxHash).
/// quick_hash: reads head + tail bytes only (fast filter).
/// full_hash: streaming read of the entire file.
/// </summary>
public static class Hasher
{
    /// <summary>XXH128 hash of head + tail bytes. Returns 16 bytes or null.</summary>
    public static byte[]? QuickHash(string filePath)
    {
        if (!File.Exists(filePath)) return null;
        try
        {
            var fileSize = new FileInfo(filePath).Length;
            var hasher = XXH128.DigestOf(Array.Empty<byte>()); // placehold, we'll build incrementally

            using var fs = new FileStream(filePath, FileMode.Open, FileAccess.Read, FileShare.Read);
            var headSize = (int)Math.Min(Config.QuickHashHeadBytes, fileSize);
            var tailSize = (int)Math.Min(Config.QuickHashTailBytes, fileSize - headSize);

            // Read head
            if (headSize > 0)
            {
                var buf = new byte[headSize];
                fs.ReadExactly(buf);
                hasher = XXH128.DigestOf(buf);
            }

            // Seek to tail and re-hash combined
            if (tailSize > 0)
            {
                fs.Seek(-tailSize, SeekOrigin.End);
                var tailBuf = new byte[tailSize];
                fs.ReadExactly(tailBuf);

                // Combine head + tail into one hash
                var combined = new byte[headSize + tailSize];
                if (headSize > 0)
                {
                    fs.Seek(0, SeekOrigin.Begin);
                    fs.ReadExactly(combined, 0, headSize);
                }
                Array.Copy(tailBuf, 0, combined, headSize, tailSize);
                return XXH128.DigestOf(combined);
            }

            return hasher;
        }
        catch { return null; }
    }

    /// <summary>XXH128 hash of the entire file (streaming).</summary>
    public static byte[]? FullHash(string filePath)
    {
        if (!File.Exists(filePath)) return null;
        try
        {
            using var fs = new FileStream(filePath, FileMode.Open, FileAccess.Read, FileShare.Read);
            var buf = new byte[Config.HashReadChunk];
            var hasher = new XXH128();
            int read;
            while ((read = fs.Read(buf, 0, buf.Length)) > 0)
                hasher.Update(buf.AsSpan(0, read));
            return hasher.Digest();
        }
        catch { return null; }
    }
}
