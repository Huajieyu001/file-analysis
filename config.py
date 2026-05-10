"""
全局配置常量
-----------
所有可调参数集中在此文件，CLI / Web / 桌面客户端均读取此配置。
"""

import os

# ---- 数据库 ----
# SQLite 数据库文件路径，默认存放在项目根目录
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dedup.db")

# ---- 哈希计算 ----
# 快速哈希：仅读取文件头尾各 64KB，用于快速排除不同内容的同大小文件
QUICK_HASH_HEAD_BYTES = 64 * 1024   # 文件头读取量
QUICK_HASH_TAIL_BYTES = 64 * 1024   # 文件尾读取量
# 完整哈希：流式读取时的缓冲区大小
HASH_READ_CHUNK = 1024 * 1024       # 1MB 块

# ---- 并发 ----
# I/O 密集型任务（读文件算哈希）的并行线程数
WORKER_THREADS = 4

# ---- 目录过滤 ----
# 遍历文件时自动跳过这些目录（系统/临时目录）
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

# ---- 文件大小过滤 ----
# 小于此值的文件直接跳过（单位 MB，0 = 不限制）
# 视频去重场景建议 100-200MB 以上
MIN_FILE_SIZE_MB = 200
MIN_FILE_SIZE = MIN_FILE_SIZE_MB * 1024 * 1024  # 内部使用的字节值

# ---- 文件后缀过滤 ----
# 仅扫描这些后缀的文件，留空 set() 则扫描所有类型
# CLI/桌面客户端可通过 --extensions 参数覆盖
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

# ---- 系统文件 ----
# 无论后缀过滤如何配置，始终跳过的文件扩展名
SKIP_EXTENSIONS = {".DS_Store", ".Thumbs.db", ".thumb", ".ini"}
