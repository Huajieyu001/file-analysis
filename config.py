import os

# Database
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dedup.db")

# Hashing
QUICK_HASH_HEAD_BYTES = 64 * 1024   # 64KB from file head
QUICK_HASH_TAIL_BYTES = 64 * 1024   # 64KB from file tail
HASH_READ_CHUNK = 1024 * 1024       # 1MB chunks for full_hash streaming

# Concurrency
WORKER_THREADS = 4

# Directories to skip
SKIP_DIRS = {
    "$RECYCLE.BIN",
    "System Volume Information",
    "Windows",
    "Program Files",
    "Program Files (x86)",
    "ProgramData",
    "Recovery",
    ".git",
    "__pycache__",
    "node_modules",
}

# File size limit in MB. Files smaller than this are skipped.
# 0 = no limit. Example: 10 = skip files under 10 MB.
MIN_FILE_SIZE_MB = 0
MIN_FILE_SIZE = MIN_FILE_SIZE_MB * 1024 * 1024

# Only scan files with these extensions. Empty = scan all.
SCAN_EXTENSIONS = {
    # --- 主流容器 ---
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".3gp", ".3g2", ".ogv", ".ogm", ".asf", ".f4v",
    ".divx", ".xvid", ".rm", ".rmvb",
    # --- 流媒体 / 传输流 ---
    ".ts", ".m2ts", ".mts", ".m2t", ".m2v", ".m2p",
    ".mpv", ".m1v", ".mpe", ".mp4v",
    # --- 光盘镜像 ---
    ".vob", ".evo", ".vro", ".ifo", ".bdmv", ".mpls",
    # --- 专业/影视 ---
    ".mxf", ".braw", ".r3d", ".ari", ".arx", ".dpx", ".cin",
    ".dng", ".insv", ".avchd",
    # --- 摄像机 / 手持设备 ---
    ".mod", ".tod", ".svo", ".vr", ".vrcam", ".dav",
    # --- 监控 DVR/NVR ---
    ".h264", ".h265", ".264", ".265", ".avc", ".hevc", ".bvr",
    # --- 视频编辑/代理 ---
    ".yuv", ".vdr", ".pva", ".nsv", ".nut", ".roq",
    ".bik", ".smk", ".swf",
    # --- IPTV / 录播 ---
    ".wtv", ".dvr-ms",
    # --- 其他 ---
    ".m4p", ".m4b", ".cpi", ".clpi",
}


# System files always skipped regardless of SCAN_EXTENSIONS
SKIP_EXTENSIONS = {".DS_Store", ".Thumbs.db", ".thumb", ".ini"}
