#!/usr/bin/env python3
"""
文件去重工具 — 命令行入口
===========================

三段式去重：按大小分组 → 快速哈希 → 完整哈希(可选)
结果持久化到 SQLite，支持增量扫描。

用法示例：
  # 扫描指定盘符
  python dedup.py scan --drives D E F

  # 扫描指定目录
  python dedup.py scan --paths D:/Videos E:/Backup

  # 增量扫描（自动跳过未变化文件，默认行为）
  python dedup.py scan --drives D E F

  # 强制全量重扫
  python dedup.py scan --drives D E F --force

  # 全量哈希精确模式（默认快速模式跳过低速的全文件哈希）
  python dedup.py scan --drives D E F --full-hash

  # 只扫描特定后缀
  python dedup.py scan --drives D E F -e .mp4 .mkv

  # 导出报告
  python dedup.py report -o dupes.csv
  python dedup.py report -o dupes.json
  python dedup.py report --interactive

  # 查看统计 / 清理数据库
  python dedup.py stats
  python dedup.py clean
"""

import argparse
import os
import sys
from datetime import datetime

# Windows 终端可能使用 GBK 编码，强制 UTF-8 避免路径中的特殊字符报错
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import DB_PATH, SCAN_EXTENSIONS, MIN_FILE_SIZE_MB


def _make_output_name(ext=".csv", label=""):
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    label = f"-{label}" if label else ""
    return f"dupes{label}-{ts}{ext}"
from database import Database
from deduplicator import run_dedup
from reporter import (
    export_csv,
    export_json,
    interactive_mode,
    print_stats,
    print_summary,
    format_size,
)


def cmd_scan(args):
    """Scan for duplicates."""
    db = Database(args.db)

    # Resolve paths
    paths = []
    if args.drives:
        for drive in args.drives:
            drive = drive.strip().rstrip(":\\/")
            if sys.platform == "win32":
                p = f"{drive}:\\"
            else:
                p = f"/{drive}"
            if os.path.exists(p):
                paths.append(p)
            else:
                print(f"Warning: drive/path not found: {p}")

    if args.paths:
        for p in args.paths:
            p = os.path.abspath(os.path.expanduser(p))
            if os.path.exists(p):
                paths.append(p)
            else:
                print(f"Warning: path not found: {p}")

    if not paths:
        print("Error: no valid paths to scan. Use --paths or --drives.")
        sys.exit(1)

    # Resolve extensions filter
    extensions = None
    if args.extensions:
        extensions = {e.strip().lower() if e.strip().startswith(".") else f".{e.strip().lower()}"
                      for e in args.extensions}
    elif SCAN_EXTENSIONS:
        extensions = SCAN_EXTENSIONS

    print(f"Scanning {len(paths)} path(s):")
    for p in paths:
        print(f"  {p}")
    print(f"Database: {args.db}")
    if extensions:
        print(f"Extensions filter: {' '.join(sorted(extensions))}")
    else:
        print("Extensions filter: all files")
    if MIN_FILE_SIZE_MB > 0:
        print(f"Min file size: {MIN_FILE_SIZE_MB} MB")
    else:
        print("Min file size: no limit")
    if args.force:
        print("Force mode: will re-hash all files.")
    else:
        print("Incremental mode: skipping unchanged files.")
    print()

    db.init_db()

    def progress(stage, msg):
        if stage == "done":
            print(f"\n{msg}")
        elif "progress" in stage:
            print(f"\r  {msg}", end="", flush=True)
        else:
            print(f"  {msg}")

    duplicate_groups = run_dedup(
        paths, db, force=args.force, progress_callback=progress, extensions=extensions,
        fast_mode=not args.full_hash,
    )

    print_summary(duplicate_groups)

    if duplicate_groups:
        if args.interactive:
            interactive_mode(duplicate_groups)
        elif args.output:
            out_path = _make_output_name() if args.output == "__auto__" else args.output
            ext = os.path.splitext(out_path)[1].lower()
            if ext == ".csv":
                export_csv(duplicate_groups, out_path)
                print(f"\nReport exported to {out_path} (CSV)")
            elif ext == ".json":
                export_json(duplicate_groups, out_path)
                print(f"\nReport exported to {out_path} (JSON)")
            else:
                print(f"Unknown format: {ext}. Use .csv or .json")
        else:
            print("\nUse --output to export or --interactive for interactive mode.")

    db.close()


def cmd_report(args):
    """Generate report from existing database."""
    db = Database(args.db)

    duplicate_groups = db.get_duplicate_groups()

    if not duplicate_groups:
        print("No duplicate files found in database.")
        db.close()
        return

    print(f"Found {len(duplicate_groups)} duplicate groups.")

    if args.interactive:
        interactive_mode(duplicate_groups)
    elif args.output:
        out_path = _make_output_name() if args.output == "__auto__" else args.output
        ext = os.path.splitext(out_path)[1].lower()
        if ext == ".csv":
            count = export_csv(duplicate_groups, out_path)
            print(f"Exported {count} groups to {out_path}")
        elif ext == ".json":
            count = export_json(duplicate_groups, out_path)
            print(f"Exported {count} groups to {out_path}")
        else:
            print(f"Unknown format: {ext}. Use .csv or .json")
    else:
        print_summary(duplicate_groups)
        print("\nUse --output to export or --interactive for interactive mode.")

    db.close()


def cmd_stats(args):
    """Print database statistics."""
    db = Database(args.db)
    stats = db.get_stats()
    print_stats(stats)
    db.close()


def cmd_clean(args):
    """Clean/reset the database."""
    db = Database(args.db)

    if args.all:
        if os.path.exists(args.db):
            os.remove(args.db)
            print(f"Database deleted: {args.db}")
        else:
            print("No database found.")
    else:
        db.init_db()
        removed = db.remove_nonexistent()
        print(f"Cleaned up {removed} missing file records from database.")

    db.close()


def main():
    parser = argparse.ArgumentParser(
        description="File Deduplication Tool — find duplicate files efficiently"
    )
    parser.add_argument("--db", default=DB_PATH, help=f"SQLite database path (default: {DB_PATH})")

    sub = parser.add_subparsers(dest="command", help="Commands")

    # scan
    scan_p = sub.add_parser("scan", help="Scan directories for duplicates")
    scan_p.add_argument("--paths", nargs="+", help="Directories or files to scan")
    scan_p.add_argument("--drives", nargs="+", help="Drive letters to scan (e.g. D E F)")
    scan_p.add_argument("--force", action="store_true", help="Force re-hash all files")
    scan_p.add_argument("--full-hash", action="store_true",
                        help="Compute full file hash (slow, accurate). Default: quick hash only.")
    scan_p.add_argument("--output", "-o", nargs="?", const="__auto__",
                        help="Export report (.csv/.json). Without value, auto-generates "
                             "timestamped filename: dupes-YYYYMMDD-HHmmss.csv")
    scan_p.add_argument("--interactive", "-i", action="store_true",
                        help="Interactive mode after scan")
    scan_p.add_argument("--extensions", "-e", nargs="+",
                        help="Only scan these file extensions (e.g. '.mp4 .mkv .avi'). "
                             "Default: scan all unless configured in config.py")

    # report
    report_p = sub.add_parser("report", help="Generate report from existing database")
    report_p.add_argument("--format", choices=["csv", "json"], default="csv")
    report_p.add_argument("--output", "-o", nargs="?", const="__auto__",
                          help="Output file path (.csv/.json). Without value, auto-generates "
                               "timestamped filename: dupes-YYYYMMDD-HHmmss.csv")
    report_p.add_argument("--interactive", "-i", action="store_true",
                          help="Interactive duplicate review")

    # stats
    sub.add_parser("stats", help="Show scan statistics")

    # clean
    clean_p = sub.add_parser("clean", help="Clean database")
    clean_p.add_argument("--all", action="store_true", help="Remove entire database")

    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "report":
        cmd_report(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "clean":
        cmd_clean(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
