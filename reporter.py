"""
结果输出模块
-----------
支持三种输出方式：
  - CSV 导出，可在 Excel 中打开
  - JSON 导出，供其他工具处理
  - 终端交互式模式，逐组手工决定保留/删除
"""

import csv
import json
import os
import sys
from datetime import datetime

# Windows 终端可能使用 GBK 编码，强制 UTF-8 避免路径中的特殊字符报错
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 可读大小单位
_SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"]


def format_size(size_bytes):
    if size_bytes == 0:
        return "0 B"
    i = 0
    fsize = float(size_bytes)
    while fsize >= 1024 and i < len(_SIZE_UNITS) - 1:
        fsize /= 1024
        i += 1
    return f"{fsize:.2f} {_SIZE_UNITS[i]}"


def format_time(mtime_ns):
    dt = datetime.fromtimestamp(mtime_ns / 1e9)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def export_csv(duplicate_groups, filepath, db=None):
    """Export duplicate groups as CSV."""
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["group_id", "file_size", "file_size_human", "file_path", "mtime", "is_original"])
        for idx, (full_hash, file_size, files) in enumerate(duplicate_groups, 1):
            files_sorted = sorted(files, key=lambda x: x[2])  # sort by mtime, oldest first
            for fidx, (fpath, fsize, mtime_ns) in enumerate(files_sorted):
                writer.writerow([
                    idx,
                    fsize,
                    format_size(fsize),
                    fpath,
                    format_time(mtime_ns),
                    "yes" if fidx == 0 else "",
                ])
    return len(duplicate_groups)


def export_json(duplicate_groups, filepath, db=None):
    """Export duplicate groups as JSON."""
    output = []
    for idx, (full_hash, file_size, files) in enumerate(duplicate_groups, 1):
        files_sorted = sorted(files, key=lambda x: x[2])
        group = {
            "group_id": idx,
            "file_size": file_size,
            "file_size_human": format_size(file_size),
            "hash_hex": full_hash.hex() if full_hash else None,
            "files": [
                {
                    "path": fpath,
                    "size": fsize,
                    "mtime": format_time(mtime_ns),
                    "mtime_ns": mtime_ns,
                    "is_original": fidx == 0,
                }
                for fidx, (fpath, fsize, mtime_ns) in enumerate(files_sorted)
            ],
        }
        output.append(group)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    return len(duplicate_groups)


def interactive_mode(duplicate_groups, db=None):
    """Interactive terminal mode: show each group and let user decide."""
    total_wasted = 0
    total_groups_resolved = 0

    for idx, (full_hash, file_size, files) in enumerate(duplicate_groups, 1):
        files_sorted = sorted(files, key=lambda x: x[2])
        wasted = (len(files_sorted) - 1) * file_size

        print(f"\n{'=' * 70}")
        print(f"Group {idx}/{len(duplicate_groups)} | {len(files_sorted)} duplicates | "
              f"Wasted: {format_size(wasted)} | Each: {format_size(file_size)}")
        print(f"{'=' * 70}")

        for fidx, (fpath, fsize, mtime_ns) in enumerate(files_sorted):
            tag = " [OLDEST - suggested keep]" if fidx == 0 else ""
            print(f"  [{fidx + 1}] {fpath}")
            print(f"       Size: {format_size(fsize)} | Modified: {format_time(mtime_ns)}{tag}")

        print(f"\n  Suggested: keep [1] (oldest), delete others to save {format_size(wasted)}")
        choice = input("  Keep which? [1-N, s=skip group, q=quit]: ").strip().lower()

        if choice == "q":
            print("Exiting interactive mode.")
            break
        elif choice == "s":
            print("Skipped.")
            continue

        try:
            keep_idx = int(choice) - 1
            if 0 <= keep_idx < len(files_sorted):
                removable_count = len(files_sorted) - 1
                print(f"  -> Keeping [{keep_idx + 1}], {removable_count} file(s) to remove "
                      f"(saving {format_size(wasted - file_size)})")
                total_wasted += wasted
                total_groups_resolved += 1
            else:
                print(f"  Invalid index: {choice}")
        except ValueError:
            print(f"  Invalid input: {choice}")

    print(f"\n{'=' * 70}")
    print(f"Summary: {total_groups_resolved} groups resolved, "
          f"potential savings: {format_size(total_wasted)}")


def print_stats(stats):
    """Print scan statistics."""

    print(f"""
File Dedup Statistics
{'=' * 50}
Total files indexed:     {stats['total_files']:>10,}
  - Size-only (Pass 1):  {stats['sized']:>10,}
  - Quick-hashed:        {stats['quick_hashed']:>10,}
  - Full-hashed:         {stats['full_hashed']:>10,}
  - Skipped (errors):    {stats['skipped']:>10,}
  - Missing (removed):   {stats['missing']:>10,}

Duplicate groups found:  {stats['duplicate_groups']:>10,}
Wasted space:            {format_size(stats['wasted_bytes']):>15}
Unique data size:        {format_size(stats['unique_size']):>15}
""")


def print_summary(duplicate_groups):
    """Print a quick summary of duplicate groups."""
    if not duplicate_groups:
        print("No duplicate files found.")
        return

    total_wasted = sum(
        (len(files) - 1) * file_size
        for _, file_size, files in duplicate_groups
    )

    print(f"\nFound {len(duplicate_groups)} duplicate groups")
    print(f"Total wasted space: {format_size(total_wasted)}")

    # Show top 10 largest groups
    sorted_groups = sorted(
        duplicate_groups,
        key=lambda g: (len(g[2]) - 1) * g[1],
        reverse=True,
    )

    print(f"\nTop duplicate groups by wasted space:")
    print(f"{'Group':<6} {'Size':<12} {'Dupes':<8} {'Wasted':<12} {'Example'}")
    print("-" * 80)
    for idx, (_, file_size, files) in enumerate(sorted_groups[:10], 1):
        wasted = (len(files) - 1) * file_size
        example = files[0][0]
        if len(example) > 50:
            example = "..." + example[-47:]
        print(f"{idx:<6} {format_size(file_size):<12} {len(files):<8} "
              f"{format_size(wasted):<12} {example}")
