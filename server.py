"""
文件去重工具 — Web 服务端
==========================
FastAPI + SSE 实时推送进度。

API 端点：
  GET  /api/drives         列出可用盘符
  GET  /api/search?q=xxx   搜索文件(支持关键字)
  GET  /api/stats          数据库统计
  GET  /api/duplicates     重复文件组列表
  GET  /api/scan/status    扫描状态
  GET  /api/scan/progress  SSE 实时进度流
  POST /api/scan/start     启动扫描 {paths, force, extensions, min_size_mb}
  POST /api/scan/stop      停止扫描
  POST /api/export         导出 CSV/JSON
  POST /api/clean          清理不存在的文件记录
  DELETE /api/files         删除单个文件
  POST /api/duplicates/delete-group  删除一组重复(保留一个)

启动：python server.py  →  浏览器打开 http://localhost:8899
"""

import asyncio
import json
import os
import queue
import sys
import threading
import time

# Force UTF-8 on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from config import DB_PATH, SCAN_EXTENSIONS, MIN_FILE_SIZE_MB
from database import Database
from deduplicator import run_dedup
from reporter import print_summary, format_size, export_csv, export_json

app = FastAPI(title="File Dedup Tool")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Scan state
_scan_lock = threading.Lock()
_scan_running = False
_progress_queue = queue.Queue()


# ---- SSE ----

@app.get("/api/scan/progress")
async def scan_progress(request: Request):
    """SSE endpoint for real-time progress."""

    async def event_stream():
        while True:
            if await request.is_disconnected():
                break
            try:
                msg = _progress_queue.get(timeout=1)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get("type") == "done":
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---- Scan API ----

@app.post("/api/scan/start")
async def start_scan(data: dict):
    """Start a new scan. Body: {paths: [...], force: bool, extensions: [...], min_size_mb: int}"""
    global _scan_running

    if _scan_running:
        return {"ok": False, "error": "Scan already running"}

    paths = data.get("paths", [])
    if not paths:
        return {"ok": False, "error": "No paths specified"}

    force = data.get("force", False)
    extensions_raw = data.get("extensions", None)
    min_size_mb = data.get("min_size_mb", None)

    # Resolve extensions
    extensions = None
    if extensions_raw:
        ext_set = set()
        for e in extensions_raw:
            e = e.strip().lower()
            ext_set.add(e if e.startswith(".") else f".{e}")
        extensions = ext_set if ext_set else extensions

    # Start scan in background thread
    def _run():
        global _scan_running
        try:
            with _scan_lock:
                _scan_running = True

            # Override config for this scan
            from config import DB_PATH as dbp
            os.environ["__MIN_SIZE_MB__"] = str(min_size_mb) if min_size_mb is not None else ""

            db = Database(dbp)

            # If user specified min_size, override
            if min_size_mb is not None:
                import config as cfg
                cfg.MIN_FILE_SIZE = min_size_mb * 1024 * 1024

            db.init_db()

            def on_progress(stage, msg):
                _progress_queue.put({"type": "progress", "stage": stage, "message": msg})

            on_progress("scan_start", "Starting scan...")

            groups = run_dedup(
                paths, db,
                force=force,
                progress_callback=on_progress,
                extensions=extensions,
            )

            dup_list = []
            total_wasted = 0
            for fhash, fsize, files in groups:
                wasted = (len(files) - 1) * fsize
                total_wasted += wasted
                dup_list.append({
                    "hash": fhash.hex() if fhash else "",
                    "size": fsize,
                    "size_human": format_size(fsize),
                    "count": len(files),
                    "wasted": format_size(wasted),
                    "files": [
                        {"path": fp, "size": fs, "mtime_ns": mt}
                        for fp, fs, mt in files
                    ],
                })

            dup_list.sort(key=lambda g: g["count"] * g["size"], reverse=True)

            _progress_queue.put({
                "type": "done",
                "total_files": len(db.existing_paths_map()),
                "duplicate_groups": len(dup_list),
                "total_wasted": format_size(total_wasted),
                "top_groups": dup_list[:20],
            })

            db.close()
        except Exception as e:
            _progress_queue.put({"type": "error", "message": str(e)})
        finally:
            _scan_running = False

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True}


@app.post("/api/scan/stop")
async def stop_scan():
    """Stop a running scan."""
    global _scan_running
    _scan_running = False
    _progress_queue.put({"type": "stopped", "message": "Scan stopped by user"})
    return {"ok": True}


@app.get("/api/scan/status")
async def scan_status():
    return {"running": _scan_running}


# ---- Data API ----

@app.get("/api/drives")
async def list_drives():
    """List available drive letters (Windows) or root dirs."""
    import string
    drives = []
    for d in string.ascii_uppercase:
        p = f"{d}:\\"
        if os.path.exists(p):
            drives.append({"letter": d, "path": p})
    return {"drives": drives}


@app.get("/api/stats")
async def get_stats():
    try:
        db = Database()
        stats = db.get_stats()
        db.close()
        return {"ok": True, "stats": {
            "total_files": stats["total_files"],
            "duplicate_groups": stats["duplicate_groups"],
            "wasted_bytes": stats["wasted_bytes"],
            "wasted_human": format_size(stats["wasted_bytes"]),
            "unique_size_human": format_size(stats["unique_size"]),
            "full_hashed": stats["full_hashed"],
            "sized": stats["sized"],
        }}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/duplicates")
async def get_duplicates(offset: int = 0, limit: int = 500):
    try:
        db = Database()
        groups = db.get_duplicate_groups()
        groups.sort(key=lambda g: (len(g[2]) - 1) * g[1], reverse=True)

        total = len(groups)
        page = groups[offset:offset + limit]

        result = []
        for fhash, fsize, files in page:
            wasted = (len(files) - 1) * fsize
            files_sorted = sorted(files, key=lambda x: x[2])
            result.append({
                "hash": fhash.hex() if fhash else "",
                "size": fsize,
                "size_human": format_size(fsize),
                "count": len(files),
                "wasted": format_size(wasted),
                "wasted_bytes": wasted,
                "files": [
                    {"path": fp, "size": fs, "size_human": format_size(fs), "mtime_ns": mt}
                    for fp, fs, mt in files_sorted
                ],
            })

        db.close()
        return {"ok": True, "total": total, "offset": offset, "groups": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/export")
async def export_report(data: dict):
    """Export to CSV or JSON. Body: {format: 'csv'|'json'}"""
    fmt = data.get("format", "csv")
    ts = time.strftime("%Y%m%d-%H%M%S")
    fname = f"dupes-{ts}.{fmt}"
    fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)

    try:
        db = Database()
        groups = db.get_duplicate_groups()
        if fmt == "csv":
            export_csv(groups, fpath, db)
        else:
            export_json(groups, fpath, db)
        db.close()
        return {"ok": True, "filename": fname, "path": fpath}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/clean")
async def clean_db():
    try:
        db = Database()
        db.init_db()
        removed = db.remove_nonexistent()
        db.close()
        return {"ok": True, "removed": removed}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---- Delete API ----

@app.delete("/api/files")
async def delete_file(data: dict):
    """Delete a single file from disk and DB. Body: {path: '...'}"""
    fp = data.get("path", "")
    if not fp:
        return {"ok": False, "error": "No path"}
    try:
        if os.path.exists(fp):
            os.remove(fp)
        db = Database()
        db.conn.execute("DELETE FROM file_index WHERE file_path = ?", (fp,))
        db.conn.commit()
        db.close()
        return {"ok": True, "deleted": fp}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/duplicates/delete-group")
async def delete_dup_group(data: dict):
    """Delete all but one file in a duplicate group.
    Body: {hash: 'xxx', keep: 'oldest'|'newest', paths: [...]}"""
    fhash = data.get("hash", "")
    keep = data.get("keep", "oldest")
    paths = data.get("paths", [])

    if not paths or len(paths) < 2:
        return {"ok": False, "error": "Need at least 2 files"}

    # Build (path, mtime_ns) pairs
    db = Database()
    rows = db.conn.execute(
        "SELECT file_path, mtime_ns FROM file_index WHERE full_hash = ?",
        (bytes.fromhex(fhash) if fhash else None,),
    ).fetchall()

    if not rows:
        db.close()
        return {"ok": False, "error": "Group not found in DB"}

    # Sort by mtime
    rows.sort(key=lambda r: r[1])  # oldest first
    if keep == "newest":
        rows.reverse()

    deleted = []
    errors = []
    for fp, _ in rows[1:]:  # Keep first, delete rest
        try:
            if os.path.exists(fp):
                os.remove(fp)
            db.conn.execute("DELETE FROM file_index WHERE file_path = ?", (fp,))
            deleted.append(fp)
        except Exception as e:
            errors.append(f"{fp}: {e}")

    db.conn.commit()
    db.close()

    total_saved = 0
    for fp in deleted:
        for p in paths:
            if p == fp:
                total_saved += 0  # We don't have size handy in deleted list
                break

    return {"ok": True, "kept": rows[0][0], "deleted": deleted, "errors": errors}


# ---- Search API ----

@app.get("/api/search")
async def search_files(q: str = Query(..., min_length=1), limit: int = Query(200)):
    """Search files by name/path keyword."""
    try:
        db = Database()
        rows = db.search_files(q, limit=limit)
        files = []
        for fp, fsize, mtime_ns, fhash, status, dup_count in rows:
            files.append({
                "path": fp,
                "size": fsize,
                "size_human": format_size(fsize),
                "mtime_ns": mtime_ns,
                "hash": fhash.hex() if fhash else None,
                "status": status,
                "duplicates": max(0, dup_count),
            })
        db.close()
        return {"ok": True, "query": q, "count": len(files), "files": files}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---- Static files ----

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    html_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(html_path):
        return HTMLResponse(open(html_path, encoding="utf-8").read())
    return HTMLResponse("<h1>index.html not found</h1>", status_code=404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8899)
