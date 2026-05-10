#!/usr/bin/env python3
"""
File Deduplication Tool

Finds duplicate files across large storage systems using a multi-pass strategy:
  1. Group by file size
  2. Quick hash (head + tail bytes)
  3. Full hash (XXH128)
Results are persisted in SQLite for incremental rescans.

Usage:
  python dedup.py scan --paths D:/Videos E:/Backup
  python dedup.py report --format csv --output dupes.csv
  python dedup.py report --interactive
  python dedup.py stats
"""

import argparse
import os
import sys

# Force UTF-8 on Windows to handle paths with non-GBK characters
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import DB_PATH, SCAN_EXTENSIONS, MIN_FILE_SIZE_MB
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
        paths, db, force=args.force, progress_callback=progress, extensions=extensions
    )

    print_summary(duplicate_groups)

    if duplicate_groups:
        if args.interactive:
            interactive_mode(duplicate_groups)
        elif args.output:
            ext = os.path.splitext(args.output)[1].lower()
            if ext == ".csv":
                export_csv(duplicate_groups, args.output)
                print(f"\nReport exported to {args.output} (CSV)")
            elif ext == ".json":
                export_json(duplicate_groups, args.output)
                print(f"\nReport exported to {args.output} (JSON)")
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
        ext = os.path.splitext(args.output)[1].lower()
        if ext == ".csv":
            count = export_csv(duplicate_groups, args.output)
            print(f"Exported {count} groups to {args.output}")
        elif ext == ".json":
            count = export_json(duplicate_groups, args.output)
            print(f"Exported {count} groups to {args.output}")
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
        db.mark_all_missing()
        db.remove_missing()
        print("Cleaned up missing file records from database.")

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
    scan_p.add_argument("--output", "-o", help="Export report to file (.csv or .json)")
    scan_p.add_argument("--interactive", "-i", action="store_true",
                        help="Interactive mode after scan")
    scan_p.add_argument("--extensions", "-e", nargs="+",
                        help="Only scan these file extensions (e.g. '.mp4 .mkv .avi'). "
                             "Default: scan all unless configured in config.py")

    # report
    report_p = sub.add_parser("report", help="Generate report from existing database")
    report_p.add_argument("--format", choices=["csv", "json"], default="csv")
    report_p.add_argument("--output", "-o", help="Output file path")
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
