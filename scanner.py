import os
from config import SKIP_DIRS, SKIP_EXTENSIONS, MIN_FILE_SIZE, SCAN_EXTENSIONS


def walk_files(paths, extensions=None):
    """Generator that yields (file_path, file_size, mtime_ns) for regular files.

    Args:
        paths: list of directory or file paths to scan.
        extensions: set of extensions to include (e.g. {'.mp4', '.mkv'}).
                    None = use SCAN_EXTENSIONS from config.
                    Empty set = scan all extensions.
    """
    if extensions is None:
        extensions = SCAN_EXTENSIONS

    for base_path in paths:
        base_path = os.path.abspath(os.path.expanduser(base_path))
        if os.path.isfile(base_path):
            stat = _safe_stat(base_path)
            if stat and _should_include(base_path, stat, extensions):
                yield base_path, stat.st_size, stat.st_mtime_ns
            continue

        for root, dirs, files in os.walk(base_path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

            for fname in files:
                fpath = os.path.join(root, fname)
                stat = _safe_stat(fpath)
                if stat and _should_include(fpath, stat, extensions):
                    yield fpath, stat.st_size, stat.st_mtime_ns


def _safe_stat(filepath):
    try:
        return os.stat(filepath)
    except (OSError, PermissionError, FileNotFoundError):
        return None


def _should_include(filepath, stat, extensions):
    if stat.st_size < MIN_FILE_SIZE:
        return False
    _, ext = os.path.splitext(filepath)
    ext = ext.lower()
    if ext in SKIP_EXTENSIONS:
        return False
    if extensions and ext not in extensions:
        return False
    return True


def count_files(paths, extensions=None):
    """Quick count of total files (for progress estimation)."""
    count = 0
    for _ in walk_files(paths, extensions):
        count += 1
    return count
