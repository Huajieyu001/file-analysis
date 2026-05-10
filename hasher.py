import os
import xxhash
from config import QUICK_HASH_HEAD_BYTES, QUICK_HASH_TAIL_BYTES, HASH_READ_CHUNK


def quick_hash(filepath):
    """Compute XXH128 hash of head + tail bytes of a file.

    Returns bytes (16 bytes for XXH128), or None if file can't be read.
    """
    try:
        file_size = os.path.getsize(filepath)
    except OSError:
        return None

    h = xxhash.xxh128()
    head_size = min(QUICK_HASH_HEAD_BYTES, file_size)
    tail_size = min(QUICK_HASH_TAIL_BYTES, file_size - head_size)

    try:
        with open(filepath, "rb") as f:
            # Read head
            if head_size > 0:
                _hash_stream(f, head_size, h)

            # Seek to tail
            if tail_size > 0:
                f.seek(-tail_size, os.SEEK_END)
                _hash_stream(f, tail_size, h)

        return h.digest()
    except (OSError, PermissionError, FileNotFoundError):
        return None


def full_hash(filepath):
    """Compute XXH128 hash of the entire file (streaming).

    Returns bytes (16 bytes), or None if file can't be read.
    """
    h = xxhash.xxh128()
    try:
        with open(filepath, "rb") as f:
            _hash_stream(f, float("inf"), h)
        return h.digest()
    except (OSError, PermissionError, FileNotFoundError):
        return None


def _hash_stream(f, max_bytes, hasher):
    """Read up to max_bytes from file f into hasher."""
    remaining = max_bytes
    while remaining > 0:
        chunk_size = min(HASH_READ_CHUNK, remaining)
        data = f.read(chunk_size)
        if not data:
            break
        hasher.update(data)
        remaining -= len(data)
