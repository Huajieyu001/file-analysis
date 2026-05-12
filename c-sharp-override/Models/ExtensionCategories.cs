namespace CleanDup.Models;

/// <summary>
/// File extension categories for the settings UI.
/// Mirrors the Python VIDEO_EXTENSIONS / IMAGE_EXTENSIONS etc.
/// </summary>
public static class ExtensionCategories
{
    public static readonly Dictionary<string, string[]> Video = new()
    {
        ["主流容器"] = [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
                        ".mpg", ".mpeg", ".3gp", ".3g2", ".ogv", ".ogm", ".asf", ".f4v",
                        ".divx", ".xvid", ".rm", ".rmvb"],
        ["流媒体/传输流"] = [".ts", ".m2ts", ".mts", ".m2t", ".m2v", ".m2p", ".mpv", ".m1v", ".mpe", ".mp4v"],
        ["光盘镜像"] = [".vob", ".evo", ".vro", ".ifo", ".bdmv", ".mpls"],
        ["专业/影视"] = [".mxf", ".braw", ".r3d", ".ari", ".arx", ".dpx", ".cin", ".dng", ".insv", ".avchd"],
        ["摄像/手持"] = [".mod", ".tod", ".svo", ".vr", ".vrcam", ".dav"],
        ["监控 DVR/NVR"] = [".h264", ".h265", ".264", ".265", ".avc", ".hevc", ".bvr"],
        ["视频编辑/代理"] = [".yuv", ".vdr", ".pva", ".nsv", ".nut", ".roq", ".bik", ".smk", ".swf"],
        ["IPTV/录播"] = [".wtv", ".dvr-ms"],
        ["其他视频"] = [".m4p", ".m4b", ".cpi", ".clpi"],
    };

    public static readonly Dictionary<string, string[]> Image = new()
    {
        ["常见图片"] = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".svg"],
        ["RAW/专业"] = [".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".pef", ".raf"],
        ["其他图片"] = [".ico", ".heic", ".heif", ".psd", ".ai", ".eps"],
    };

    public static readonly Dictionary<string, string[]> Audio = new()
    {
        ["常见音频"] = [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus"],
        ["无损/专业"] = [".alac", ".ape", ".aiff", ".dsf", ".dff", ".pcm"],
        ["其他音频"] = [".mid", ".midi", ".amr", ".ac3", ".dts"],
    };

    public static readonly Dictionary<string, string[]> Doc = new()
    {
        ["文档"] = [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv", ".md"],
        ["压缩包"] = [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"],
        ["代码/配置"] = [".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".yaml", ".ini", ".cfg"],
    };

    /// <summary>Top-level category name → sub-categories</summary>
    public static readonly Dictionary<string, Dictionary<string, string[]>> All = new()
    {
        ["视频"] = Video,
        ["图片"] = Image,
        ["音频"] = Audio,
        ["文档/其他"] = Doc,
    };

    /// <summary>Default extension set (all video types)</summary>
    public static HashSet<string> DefaultExtensions()
    {
        var set = new HashSet<string>();
        foreach (var exts in Video.Values)
            foreach (var e in exts) set.Add(e);
        return set;
    }
}
