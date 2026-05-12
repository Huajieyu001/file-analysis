"""
文件遍历模块
-----------
使用 os.scandir 替代 os.walk，利用 DirEntry 缓存的 stat 信息，
避免每个文件额外一次系统调用，遍历速度提升 3-10 倍。
"""

import os
from config import SKIP_DIRS, SKIP_EXTENSIONS, MIN_FILE_SIZE, SCAN_EXTENSIONS


def walk_files(paths, extensions=None):
    """Generator that yields (file_path, file_size, mtime_ns) for regular files.

    Uses os.scandir for cached stat (one syscall per entry, not two).
    """
    if extensions is None:
        extensions = SCAN_EXTENSIONS

    for base_path in paths:
        base_path = os.path.abspath(os.path.expanduser(base_path))

        if os.path.isfile(base_path):
            try:
                st = os.stat(base_path)
                if _should_include(base_path, st.st_size, extensions):
                    yield base_path, st.st_size, st.st_mtime_ns
            except OSError:
                pass
            continue

        if not os.path.isdir(base_path):
            continue

        stack = [base_path]
        while stack:
            try:
                with os.scandir(stack.pop()) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                name = entry.name
                                if name not in SKIP_DIRS and not name.startswith('.'):
                                    stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                st = entry.stat()
                                if _should_include(entry.path, st.st_size, extensions):
                                    yield entry.path, st.st_size, st.st_mtime_ns
                        except OSError:
                            continue
            except OSError:
                continue


def _should_include(path, file_size, extensions):
    if file_size < MIN_FILE_SIZE:
        return False
    _, ext = os.path.splitext(path)
    ext = ext.lower()
    if ext in SKIP_EXTENSIONS:
        return False
    if extensions and ext not in extensions:
        return False
    return True
