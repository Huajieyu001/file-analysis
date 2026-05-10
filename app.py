#!/usr/bin/env python3
"""
文件去重工具 — 桌面客户端
==========================
基于 CustomTkinter 的现代深色主题桌面应用。

设计理念参考 Everything：
  启动即自动增量扫描，无需手动点击"开始"
  搜索框即时过滤，输入即显示结果
  "增量更新"只处理变化的文件，"全量刷新"重扫全部

核心流程：
  1. 启动 → 自动增量扫描 → 状态显示"● 扫描中..."
  2. 扫描完成 → 状态变为"✓ 已是最新"
  3. 搜索框输入关键字 → 实时过滤显示
  4. 重复组标签页 → 查看/删除重复文件
  5. 手动点击"增量更新"/"全量刷新"触发重扫

运行方式：
  python app.py
  (可配合 PyInstaller 打包为独立 .exe)
"""
import os, sys, time, queue, threading, json, re, tkinter as tk
from tkinter import messagebox, filedialog
import customtkinter as ctk
from datetime import datetime

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import DB_PATH, MIN_FILE_SIZE_MB
from database import Database
from deduplicator import run_dedup
from reporter import format_size

MB = 1024 * 1024

VIDEO_EXTENSIONS = {
    "主流容器": [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
                  ".mpg", ".mpeg", ".3gp", ".3g2", ".ogv", ".ogm", ".asf", ".f4v",
                  ".divx", ".xvid", ".rm", ".rmvb"],
    "流媒体/传输流": [".ts", ".m2ts", ".mts", ".m2t", ".m2v", ".m2p", ".mpv", ".m1v", ".mpe", ".mp4v"],
    "光盘镜像": [".vob", ".evo", ".vro", ".ifo", ".bdmv", ".mpls"],
    "专业/影视": [".mxf", ".braw", ".r3d", ".ari", ".arx", ".dpx", ".cin", ".dng", ".insv", ".avchd"],
    "摄像/手持": [".mod", ".tod", ".svo", ".vr", ".vrcam", ".dav"],
    "监控 DVR/NVR": [".h264", ".h265", ".264", ".265", ".avc", ".hevc", ".bvr"],
    "视频编辑/代理": [".yuv", ".vdr", ".pva", ".nsv", ".nut", ".roq", ".bik", ".smk", ".swf"],
    "IPTV/录播": [".wtv", ".dvr-ms"],
    "其他": [".m4p", ".m4b", ".cpi", ".clpi"],
}

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

# ─── Main App ────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("文件去重工具")
        self.geometry("1120x740")
        self.minsize(950, 550)

        self.scan_running = False
        self.scan_force = False
        self.db = Database()
        self.progress_queue = queue.Queue()
        self.dup_groups = []
        self.drive_chips = {}
        self.active_exts = set()

        self._build_ui()
        self._load_settings()
        self._poll_progress()
        # Defer data load + auto scan so window appears instantly
        self.after(300, self._load_data_async)
        self.after(500, self._start_incremental)

    # ── Build ───────────────────────────────────────────────────────────

    def _build_ui(self):
        # -- Top bar --
        top = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        top.pack(fill="x", padx=20, pady=(16, 8))

        ctk.CTkLabel(top, text="文件去重工具", font=ctk.CTkFont(size=20, weight="bold"),
                     text_color="#8be9fd").pack(side="left")

        self.status_lbl = ctk.CTkLabel(top, text="", font=ctk.CTkFont(size=12),
                                       text_color="#6272a4")
        self.status_lbl.pack(side="right")

        # -- Search bar --
        search_card = ctk.CTkFrame(self, corner_radius=10, border_width=1,
                                   border_color="#2a2f40", fg_color="#1a1d29")
        search_card.pack(fill="x", padx=20, pady=(0, 6))

        sr = ctk.CTkFrame(search_card, fg_color="transparent")
        sr.pack(fill="x", padx=14, pady=10)

        ctk.CTkLabel(sr, text="🔍", font=ctk.CTkFont(size=14)).pack(side="left", padx=(0, 8))

        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *a: self._on_search())
        se = ctk.CTkEntry(sr, textvariable=self.search_var, height=34,
                          placeholder_text="搜索文件名或路径... 留空显示全部",
                          fg_color="#11131c", border_color="#2a2f40",
                          corner_radius=8, font=ctk.CTkFont(size=13))
        se.pack(side="left", fill="x", expand=True)

        self.search_count_lbl = ctk.CTkLabel(sr, text="", font=ctk.CTkFont(size=11),
                                             text_color="#6272a4")
        self.search_count_lbl.pack(side="right", padx=(10, 0))

        ctk.CTkButton(sr, text="✕", width=30, height=30, fg_color="transparent",
                      hover_color="#2a2f40", corner_radius=6, text_color="#6272a4",
                      font=ctk.CTkFont(size=14),
                      command=lambda: self.search_var.set("")).pack(side="right")

        # -- Controls --
        ctl = ctk.CTkFrame(self, corner_radius=10, border_width=1,
                           border_color="#2a2f40", fg_color="#1a1d29")
        ctl.pack(fill="x", padx=20, pady=(0, 6))

        c1 = ctk.CTkFrame(ctl, fg_color="transparent")
        c1.pack(fill="x", padx=14, pady=(10, 6))

        # Drives
        ctk.CTkLabel(c1, text="盘符", font=ctk.CTkFont(size=12),
                     text_color="#6272a4").pack(side="left", padx=(0, 8))
        self.drive_frame = ctk.CTkFrame(c1, fg_color="transparent")
        self.drive_frame.pack(side="left")
        self._load_drives()

        # Separator
        ctk.CTkFrame(c1, width=1, height=20, fg_color="#2a2f40").pack(
            side="left", padx=14)

        # Options
        self.full_hash_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(c1, text="全量哈希（慢）", variable=self.full_hash_var,
                        font=ctk.CTkFont(size=11), text_color="#6272a4",
                        border_color="#3a4055", checkmark_color="#11131c",
                        fg_color="#8be9fd", hover_color="#6272a4",
                        width=20, height=20, corner_radius=4).pack(side="left", padx=(0, 12))

        ctk.CTkLabel(c1, text="最小", font=ctk.CTkFont(size=11),
                     text_color="#6272a4").pack(side="left")
        self.min_size_var = ctk.StringVar(value=str(MIN_FILE_SIZE_MB))
        ctk.CTkEntry(c1, textvariable=self.min_size_var, width=55, height=28,
                     corner_radius=6, font=ctk.CTkFont(size=12),
                     fg_color="#11131c", border_color="#2a2f40").pack(side="left", padx=4)
        ctk.CTkLabel(c1, text="MB", font=ctk.CTkFont(size=11),
                     text_color="#6272a4").pack(side="left", padx=(0, 12))

        self.ext_btn = ctk.CTkButton(c1, text="0 个后缀", width=90, height=28,
                                     corner_radius=6, font=ctk.CTkFont(size=11),
                                     fg_color="#1e2240", hover_color="#2a2f50",
                                     text_color="#8be9fd", border_color="#2a3f60",
                                     border_width=1,
                                     command=self._open_settings)
        self.ext_btn.pack(side="left")

        # Buttons
        c2 = ctk.CTkFrame(ctl, fg_color="transparent")
        c2.pack(fill="x", padx=14, pady=(4, 10))

        self.scan_status = ctk.CTkLabel(c2, text="", font=ctk.CTkFont(size=12, weight="bold"),
                                        text_color="#f59e0b")
        self.scan_status.pack(side="left", padx=(0, 12))

        self.incr_btn = ctk.CTkButton(c2, text="↻ 增量更新", width=100, height=32,
                                      corner_radius=6, font=ctk.CTkFont(size=12),
                                      fg_color="#1e2240", hover_color="#2a2f50",
                                      text_color="#8be9fd", border_color="#2a3f60",
                                      border_width=1, command=self._start_incremental)
        self.incr_btn.pack(side="left", padx=3)

        self.refresh_btn = ctk.CTkButton(c2, text="⟳ 全量刷新", width=100, height=32,
                                         corner_radius=6, font=ctk.CTkFont(size=12),
                                         fg_color="#1e2240", hover_color="#2a2f50",
                                         text_color="#ff9e64", border_color="#3f3020",
                                         border_width=1, command=self._start_refresh)
        self.refresh_btn.pack(side="left", padx=3)

        for label, cmd in [("清理DB", self._clean_db), ("导出CSV", self._export_csv),
                           ("导出JSON", self._export_json)]:
            ctk.CTkButton(c2, text=label, width=70, height=28, corner_radius=6,
                          fg_color="transparent", hover_color="#2a2f40",
                          text_color="#6272a4", font=ctk.CTkFont(size=11),
                          command=cmd).pack(side="right", padx=3)

        # -- Progress --
        self.prog_bar = ctk.CTkProgressBar(self, height=4, corner_radius=2,
                                           fg_color="#1a1d29", border_width=0,
                                           progress_color="#8be9fd")
        self.prog_bar.pack(fill="x", padx=20, pady=(6, 0))
        self.prog_bar.set(0)

        self.prog_text = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=11),
                                      text_color="#6272a4", anchor="w")
        self.prog_text.pack(fill="x", padx=22, pady=(2, 4))

        # -- Tab view --
        self.tab = ctk.CTkTabview(self, corner_radius=10, border_width=1,
                                  border_color="#2a2f40", fg_color="#1a1d29",
                                  segmented_button_fg_color="#11131c",
                                  segmented_button_selected_color="#1e2240",
                                  segmented_button_unselected_color="#11131c",
                                  segmented_button_selected_hover_color="#2a2f50",
                                  text_color="#6272a4",
                                  text_color_disabled="#3a3f50")
        self.tab.pack(fill="both", expand=True, padx=20, pady=(6, 10))

        self.tab.add("重复组")
        self.tab.add("搜索结果")

        # Tab 1: Dup groups
        tab1 = self.tab.tab("重复组")
        self.dup_outer, self.dup_rows, self.dup_cols, self.dup_widths = \
            self._make_tree(tab1, ["大小", "数量", "浪费", "保留文件", "操作"],
                            [100, 65, 110, 400, 120])

        # Tab 2: Search
        tab2 = self.tab.tab("搜索结果")
        self.srch_outer, self.srch_rows, self.srch_cols, self.srch_widths = \
            self._make_tree(tab2, ["文件路径", "大小", "重复状态", ""],
                            [580, 100, 90, 50])

        # -- Context menu (use tkinter Menu, lighter than CTkToplevel) --
        self.ctx_menu = None  # Created on demand in _show_popup
        self._ctx_target = None

        # -- Status bar --
        self.sbar = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=11),
                                 text_color="#3a4055", anchor="w")
        self.sbar.pack(side="bottom", fill="x", padx=22, pady=(0, 8))

    def _make_tree(self, parent, cols, widths):
        """Create a styled scrollable frame with header + rows."""
        outer = ctk.CTkScrollableFrame(parent, fg_color="transparent",
                                       corner_radius=0, scrollbar_fg_color="#1a1d29",
                                       scrollbar_button_color="#2a2f40",
                                       scrollbar_button_hover_color="#3a4055")
        outer.pack(fill="both", expand=True)

        # Header
        hdr = ctk.CTkFrame(outer, fg_color="#11131c", corner_radius=6, height=34)
        hdr.pack(fill="x", pady=(0, 2))
        hdr.pack_propagate(False)

        for col, w in zip(cols, widths):
            ctk.CTkLabel(hdr, text=col, font=ctk.CTkFont(size=11, weight="bold"),
                         text_color="#6272a4", width=w,
                         anchor="w" if w > 100 else "center").pack(side="left", padx=6)

        # Rows container
        self._row_frames = {}
        self._row_data = {}
        rows_frame = ctk.CTkFrame(outer, fg_color="transparent")
        rows_frame.pack(fill="both", expand=True)

        setattr(self, f"_rows_{len(self.__dict__)}", rows_frame)

        return outer, rows_frame, cols, widths

    def _clear_rows(self, rows_frame):
        for w in rows_frame.winfo_children():
            w.destroy()

    def _add_row(self, rows_frame, cols, widths, values, row_data=None, index=0):
        bg = "#1a1d29" if index % 2 == 0 else "#1e2233"
        row = ctk.CTkFrame(rows_frame, fg_color=bg, corner_radius=4, height=36)
        row.pack(fill="x", pady=1, padx=2)

        for i, (val, w) in enumerate(zip(values, widths)):
            color = "#f8f8f2" if i < len(values) - 1 else "#ff5555"
            if i == len(values) - 1:
                # Action button
                anchor = "center"
            elif i == 0:
                anchor = "w"
                color = "#8be9fd"
            elif w > 150:
                anchor = "w"
                color = "#c0c5d4"
            else:
                anchor = "center"
            ctk.CTkLabel(row, text=str(val), font=ctk.CTkFont(size=12),
                         text_color=color, width=w, anchor=anchor).pack(
                             side="left", padx=6)

        if row_data:
            for k, v in row_data.items():
                setattr(row, k, v)

        row.bind("<Enter>", lambda e, r=row: r.configure(fg_color="#252a3d"))
        row.bind("<Leave>", lambda e, r=row: r.configure(fg_color=bg))
        return row

    # ── Drives ──────────────────────────────────────────────────────────

    def _load_drives(self):
        import string
        for d in string.ascii_uppercase:
            if os.path.exists(f"{d}:\\"):
                selected = d != "C"
                chip = ctk.CTkButton(
                    self.drive_frame, text=f"{d}:", width=40, height=26,
                    corner_radius=13, font=ctk.CTkFont(size=11, weight="bold"),
                    fg_color="#8be9fd" if selected else "#1a1d29",
                    hover_color="#a4f0ff" if selected else "#252a3d",
                    text_color="#0d1117" if selected else "#6272a4",
                    border_color="#2a2f40" if not selected else "#8be9fd",
                    border_width=1,
                    command=lambda l=d: self._toggle_drive(l))
                chip.pack(side="left", padx=2)
                setattr(chip, "_drive_letter", d)
                setattr(chip, "_selected", selected)
                self.drive_chips[d] = chip

    def _toggle_drive(self, letter):
        chip = self.drive_chips[letter]
        sel = not getattr(chip, "_selected")
        setattr(chip, "_selected", sel)
        if sel:
            chip.configure(fg_color="#8be9fd", text_color="#0d1117",
                           border_color="#8be9fd", hover_color="#a4f0ff")
        else:
            chip.configure(fg_color="#1a1d29", text_color="#6272a4",
                           border_color="#2a2f40", hover_color="#252a3d")

    def _get_selected_drives(self):
        return [f"{d}:\\" for d, ch in self.drive_chips.items()
                if getattr(ch, "_selected", False)]

    # ── Data ─────────────────────────────────────────────────────────────

    def _load_data_async(self):
        def _load():
            for _ in range(3):  # retry up to 3 times if DB locked
                try:
                    db = Database()
                    stats = db.get_stats()
                    total = stats["total_files"]
                    dupc = stats["duplicate_groups"]
                    wasted = format_size(stats["wasted_bytes"])
                    self.after(0, lambda t=total, d=dupc, w=wasted: (
                        self.sbar.configure(text=f"已索引 {t:,} 个文件  ·  {d:,} 组重复  ·  可释放 {w}"),
                        self.status_lbl.configure(text=f"{t:,} 个文件")))

                    rows = db.search_files("", limit=200) if total > 0 else []
                    results = [(fp, format_size(fs), "唯一", "✕") for fp, fs, _, _, _, _ in rows]
                    self.after(0, lambda r=results, t=total: (
                        self._render_search(r),
                        self.search_count_lbl.configure(text=f"显示前 {len(r)} / {t:,} 个文件")))

                    db.close()
                    break
                except Exception:
                    db.close() if 'db' in dir() else None
                    time.sleep(0.5)
        threading.Thread(target=_load, daemon=True).start()

    # ── Scan ─────────────────────────────────────────────────────────────

    def _start_incremental(self):
        """Incremental scan: skip unchanged files."""
        self._run_scan(force=False)

    def _start_refresh(self):
        """Full refresh: re-hash all files."""
        if messagebox.askyesno("确认", "全量刷新将重新扫描所有文件，可能耗时较长。确定？"):
            self._run_scan(force=True)

    def _run_scan(self, force=False):
        if self.scan_running:
            return
        drives = self._get_selected_drives()
        if not drives:
            self.after(500, self._start_incremental)  # Retry later
            return

        self.scan_running = True
        self.scan_status.configure(text="● 扫描中...")
        self.incr_btn.configure(state="disabled")
        self.refresh_btn.configure(state="disabled")
        self.prog_bar.set(0)
        mode = "全量刷新" if force else "增量更新"
        self.prog_text.configure(text=f"{mode}进行中...")

        exts = self.active_exts if self.active_exts else None
        try:
            min_mb = int(self.min_size_var.get().strip() or "0")
        except ValueError:
            min_mb = 200

        def _run():
            try:
                import config as cfg
                if min_mb > 0:
                    cfg.MIN_FILE_SIZE = min_mb * MB
                self.db.init_db()

                def on_progress(stage, msg):
                    self.progress_queue.put(("progress", stage, msg))

                groups = run_dedup(drives, self.db, force=force,
                    progress_callback=on_progress, extensions=exts,
                    fast_mode=not self.full_hash_var.get())

                dup_list = []
                for fhash, fsize, files in groups:
                    dup_list.append((fhash, fsize, sorted(files, key=lambda x: x[2])))
                dup_list.sort(key=lambda g: (len(g[2]) - 1) * g[1], reverse=True)

                total = len(self.db.existing_paths_map())
                wasted = sum((len(g[2]) - 1) * g[1] for g in dup_list)
                self.progress_queue.put(("done", dup_list, total, wasted))
                self.db.close()
            except Exception as e:
                self.progress_queue.put(("error", str(e)))
            finally:
                self.scan_running = False

        threading.Thread(target=_run, daemon=True).start()

    def _poll_progress(self):
        try:
            while True:
                msg = self.progress_queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, stage, text = msg
                    pct = self._calc_pct(stage, text)
                    self.prog_bar.set(pct / 100.0)
                    pct_str = f"{pct:.2f}%"
                    self.prog_text.configure(text=f"{pct_str}  {text}")
                elif kind == "done":
                    _, dup_list, total, wasted = msg
                    self.prog_bar.set(1.0)
                    self.prog_text.configure(text="100.00%  " +
                        f"扫描完成  ·  {total:,} 个文件  ·  {len(dup_list):,} 组重复  ·  可释放 {format_size(wasted)}")
                    self.scan_status.configure(text="✓ 已是最新", text_color="#22c55e")
                    self.incr_btn.configure(state="normal")
                    self.refresh_btn.configure(state="normal")
                    self.dup_groups = dup_list
                    self._render_dups(dup_list[:200])
                    self.after(500, self._load_data_async)
                elif kind == "error":
                    self.prog_text.configure(text=f"错误: {msg[1]}")
                    self.scan_status.configure(text="✗ 扫描出错", text_color="#ff5555")
                    self.incr_btn.configure(state="normal")
                    self.refresh_btn.configure(state="normal")
        except queue.Empty:
            pass
        self.after(200, self._poll_progress)

    def _calc_pct(self, stage, text):
        """Calculate progress percentage from stage + message text."""
        # Track last known counts per pass
        if not hasattr(self, '_pass_counts'):
            self._pass_counts = {'pass1_total': 0, 'pass2_total': 0, 'pass3_total': 0}

        fast = not self.full_hash_var.get()

        if stage == "pass1_start":
            return 0.0
        elif stage == "pass1_done":
            return 35.0 if fast else 25.0
        elif stage == "pass2_start":
            return 35.0 if fast else 25.0
        elif stage == "pass2_done":
            return 100.0 if fast else 70.0
        elif stage == "pass3_start":
            return 70.0
        elif stage == "pass3_done":
            return 100.0

        # In-progress: parse "XXX/YYY" from message
        m = re.search(r"(\d+)\s*/\s*(\d+)", text)
        if not m:
            # Fallback: estimate by stage name
            if "pass1" in stage or "scan" in stage.lower():
                return 10.0 if fast else 5.0
            elif "pass2" in stage or "quick" in stage.lower():
                return 50.0 if fast else 40.0
            elif "pass3" in stage or "full" in stage.lower():
                return 85.0
            return 50.0

        cur, tot = int(m.group(1)), int(m.group(2))
        if tot == 0:
            return 50.0

        if "pass1" in stage or "scan" in stage.lower():
            base, weight = 0, 35 if fast else 25
        elif "pass2" in stage or "quick" in stage.lower():
            base, weight = 35 if fast else 25, 65 if fast else 45
        elif "pass3" in stage or "full" in stage.lower():
            base, weight = 70, 30
        else:
            base, weight = 30, 40

        return min(base + (cur / tot) * weight, 99.99)

    # ── Render ───────────────────────────────────────────────────────────

    def _render_dups(self, groups):
        outer, rows_frame, cols, widths = self.dup_outer, self.dup_rows, self.dup_cols, self.dup_widths
        self._clear_rows(rows_frame)
        for i, (fhash, fsize, files) in enumerate(groups):
            wasted = (len(files) - 1) * fsize
            keep = files[0][0]
            short = keep if len(keep) < 100 else "..." + keep[-97:]
            row = self._add_row(rows_frame, cols, widths,
                [format_size(fsize), f"{len(files)}×", format_size(wasted),
                 short, "删除"],
                {"_paths": "||".join(f[0] for f in files),
                 "_hash": fhash.hex() if fhash else ""},
                i)
        if groups:
            self.tab.set("重复组")

    def _render_search(self, results):
        outer, rows_frame, cols, widths = self.srch_outer, self.srch_rows, self.srch_cols, self.srch_widths
        self._clear_rows(rows_frame)
        for i, (fp, sh, dl, _) in enumerate(results):
            dup_color = "#ff5555" if "个重复" in dl and "0个重复" not in dl else "#6272a4"
            self._add_row(rows_frame, cols, widths,
                [fp, sh, dl, "✕"], {"_path": fp}, i)
            # Override the 4th column color
            for child in rows_frame.winfo_children()[-1].winfo_children():
                pass  # We'll style on render

    # ── Search ───────────────────────────────────────────────────────────

    def _on_search(self):
        q = self.search_var.get().strip()
        def _do():
            rows = self.db.search_files("", limit=5000) if len(q) < 2 else self.db.search_files(q, limit=200)
            label = f"全部 {len(rows):,} 个文件" if len(q) < 2 else f"找到 {len(rows)} 个"
            results = []
            for fp, fs, _, _, _, dc in rows:
                dl = f"{dc}个重复" if dc > 0 else "唯一"
                results.append((fp, format_size(fs), dl, "✕"))
            self.after(0, lambda: self._render_search(results))
            self.after(0, lambda: self.search_count_lbl.configure(text=label))
        threading.Thread(target=_do, daemon=True).start()

    # ── Click handlers ───────────────────────────────────────────────────

    def _on_dup_click(self, event):
        w = event.widget
        # Walk up to find the row frame
        while w and not hasattr(w, "_paths"):
            w = w.master if hasattr(w, 'master') else None
        if not w: return

        # Check if clicking the last column (delete button area)
        x = event.x
        if x > 850:  # approximate action column area
            self._delete_group_dups(w)
            return

        self._ctx_target = w
        # Custom right-click menu
        try:
            self._show_popup(event, is_group=True)
        except Exception:
            pass

    def _on_search_click(self, event):
        w = event.widget
        while w and not hasattr(w, "_path"):
            w = w.master if hasattr(w, 'master') else None
        if not w: return

        self._ctx_target = w
        self._show_popup(event, is_group=False)

    def _on_dup_double_click(self, event):
        w = event.widget
        while w and not hasattr(w, "_paths"):
            w = w.master if hasattr(w, 'master') else None
        if w and hasattr(w, "_paths"):
            p = getattr(w, "_paths", "").split("||")[0]
            _reveal_in_explorer(p)

    def _on_file_double_click(self, event):
        w = event.widget
        while w and not hasattr(w, "_path"):
            w = w.master if hasattr(w, 'master') else None
        if w and hasattr(w, "_path"):
            _reveal_in_explorer(getattr(w, "_path", ""))

    def _show_popup(self, event, is_group=False):
        menu = tk.Menu(self, tearoff=0, bg="#1e2233", fg="#c0c5d4",
                       activebackground="#2a2f40", activeforeground="#8be9fd",
                       font=("Segoe UI", 11), bd=0)
        menu.add_command(label=" 打开文件位置", command=self._ctx_open)
        menu.add_command(label=" 复制路径", command=self._ctx_copy)
        menu.add_separator()
        menu.add_command(label=" 删除此文件", command=self._ctx_del_one)
        if is_group:
            menu.add_command(label=" 删除该组其他重复", command=self._ctx_del_group)
        menu.post(event.x_root, event.y_root)

    def _ctx_open(self):
        if self._ctx_target:
            p = (getattr(self._ctx_target, "_paths", "") or
                 getattr(self._ctx_target, "_path", ""))
            if p:
                _reveal_in_explorer(p.split("||")[0])

    def _ctx_copy(self):
        if self._ctx_target:
            p = (getattr(self._ctx_target, "_paths", "") or
                 getattr(self._ctx_target, "_path", ""))
            p = p.split("||")[0]
            self.clipboard_clear()
            self.clipboard_append(p)
            self.status_lbl.configure(text="路径已复制")

    def _ctx_del_one(self):
        if self._ctx_target:
            p = (getattr(self._ctx_target, "_paths", "") or
                 getattr(self._ctx_target, "_path", ""))
            self._delete_file(p.split("||")[0])

    def _ctx_del_group(self):
        if self._ctx_target and hasattr(self._ctx_target, "_paths"):
            self._delete_group_dups(self._ctx_target)

    # ── Delete ───────────────────────────────────────────────────────────

    def _delete_file(self, filepath):
        if not messagebox.askyesno("确认删除", f"确定要删除此文件吗？\n\n{filepath}"):
            return
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
            db = Database()
            db.conn.execute("DELETE FROM file_index WHERE file_path=?", (filepath,))
            db.conn.commit(); db.close()
            self.status_lbl.configure(text=f"已删除: {os.path.basename(filepath)}")
            self._load_data_async()
        except Exception as e:
            messagebox.showerror("错误", f"删除失败: {e}")

    def _delete_group_dups(self, row):
        paths_str = getattr(row, "_paths", "")
        if not paths_str: return
        paths = paths_str.split("||")
        if len(paths) < 2: return
        keep, dels = paths[0], paths[1:]
        preview = "\n".join(p[:100] for p in dels[:5])
        if len(dels) > 5:
            preview += f"\n... 等共 {len(dels)} 个"
        if not messagebox.askyesno("确认删除",
            f"保留 1 个，删除其余 {len(dels)} 个\n\n保留:\n{keep}\n\n删除:\n{preview}"):
            return

        deleted = 0
        for fp in dels:
            try:
                if os.path.exists(fp): os.remove(fp)
                db = Database()
                db.conn.execute("DELETE FROM file_index WHERE file_path=?", (fp,))
                db.conn.commit(); db.close()
                deleted += 1
            except Exception as e:
                messagebox.showerror("错误", f"删除失败: {e}")
        row.destroy()
        self.status_lbl.configure(text=f"已删除 {deleted} 个重复文件")
        self._load_data_async()

    # ── Export ───────────────────────────────────────────────────────────

    def _export_csv(self):
        from reporter import export_csv
        fn = filedialog.asksaveasfilename(defaultextension=".csv",
            initialfile=f"dupes-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv",
            filetypes=[("CSV", "*.csv")])
        if fn:
            db = Database(); g = db.get_duplicate_groups()
            export_csv(g, fn, db); db.close()
            self.status_lbl.configure(text=f"已导出: {os.path.basename(fn)}")

    def _export_json(self):
        from reporter import export_json
        fn = filedialog.asksaveasfilename(defaultextension=".json",
            initialfile=f"dupes-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json",
            filetypes=[("JSON", "*.json")])
        if fn:
            db = Database(); g = db.get_duplicate_groups()
            export_json(g, fn, db); db.close()
            self.status_lbl.configure(text=f"已导出: {os.path.basename(fn)}")

    # ── Settings ──────────────────────────────────────────────────────────

    def _load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, encoding="utf-8") as f:
                    exts = set(json.load(f).get("extensions", []))
                    if exts:
                        self.active_exts = exts
            except: pass
        if not self.active_exts:
            for g in VIDEO_EXTENSIONS.values():
                self.active_exts.update(g)
        self.ext_btn.configure(text=f"{len(self.active_exts)} 个后缀")

    def _save_settings(self, extensions):
        if extensions:
            self.active_exts = extensions
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump({"extensions": sorted(extensions)}, f, ensure_ascii=False, indent=2)
            self.ext_btn.configure(text=f"{len(extensions)} 个后缀")

    def _open_settings(self):
        SettingsDialog(self, self.active_exts, self._save_settings)

    # ── Clean ─────────────────────────────────────────────────────────────

    def _clean_db(self):
        if messagebox.askyesno("确认", "清理数据库中已不存在的文件记录？"):
            db = Database(); db.init_db()
            n = db.remove_nonexistent(); db.close()
            self.status_lbl.configure(text=f"已清理 {n} 条记录")
            self._load_data_async()


# ─── Settings Dialog ─────────────────────────────────────────────────────────

class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent, current, callback):
        super().__init__(parent)
        self.callback = callback
        self.ext_vars = {}

        self.title("文件后缀设置")
        self.geometry("580x540")
        self.minsize(500, 400)
        self.grab_set()

        # Header
        ctk.CTkLabel(self, text="选择要扫描的文件后缀", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#8be9fd").pack(anchor="w", padx=20, pady=(16, 4))
        ctk.CTkLabel(self, text="未勾选的后缀在扫描时会被直接跳过",
                     font=ctk.CTkFont(size=12), text_color="#6272a4").pack(
                         anchor="w", padx=20, pady=(0, 10))

        # Quick actions
        qa = ctk.CTkFrame(self, fg_color="transparent")
        qa.pack(fill="x", padx=20, pady=(0, 8))
        ctk.CTkButton(qa, text="全选", width=70, height=26, corner_radius=6,
                      fg_color="#1e2240", hover_color="#2a2f50",
                      text_color="#8be9fd", font=ctk.CTkFont(size=11),
                      command=self._sel_all).pack(side="left", padx=(0, 4))
        ctk.CTkButton(qa, text="取消全选", width=80, height=26, corner_radius=6,
                      fg_color="#1e2240", hover_color="#2a2f50",
                      text_color="#8be9fd", font=ctk.CTkFont(size=11),
                      command=self._sel_none).pack(side="left", padx=(0, 4))
        ctk.CTkButton(qa, text="仅主流视频", width=90, height=26, corner_radius=6,
                      fg_color="#1e2240", hover_color="#2a2f50",
                      text_color="#8be9fd", font=ctk.CTkFont(size=11),
                      command=lambda: self._sel_cats(
                          ["主流容器", "流媒体/传输流"])).pack(side="left")

        # Scrollable
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent",
                                        scrollbar_fg_color="#1a1d29",
                                        scrollbar_button_color="#2a2f40",
                                        scrollbar_button_hover_color="#3a4055")
        scroll.pack(fill="both", expand=True, padx=12, pady=4)

        for ci, (cat, exts) in enumerate(VIDEO_EXTENSIONS.items()):
            cat_bg = "#1a1d29" if ci % 2 == 0 else "#1e2233"
            cf = ctk.CTkFrame(scroll, fg_color=cat_bg, corner_radius=6)
            cf.pack(fill="x", padx=4, pady=3)

            cat_var = ctk.BooleanVar(value=all(e in current for e in exts))
            cb = ctk.CTkCheckBox(cf, text=cat, variable=cat_var,
                                 font=ctk.CTkFont(size=12, weight="bold"),
                                 text_color="#c0c5d4",
                                 border_color="#3a4055", checkmark_color="#11131c",
                                 fg_color="#8be9fd", hover_color="#6272a4",
                                 command=lambda v=cat_var, c=cat: self._tgl_cat(c, v))
            cb.pack(anchor="w", padx=12, pady=(8, 4))

            ef = ctk.CTkFrame(cf, fg_color="transparent")
            ef.pack(fill="x", padx=(30, 12), pady=(0, 8))
            row = ctk.CTkFrame(ef, fg_color="transparent")
            row.pack(fill="x")
            for i, ext in enumerate(exts):
                if i > 0 and i % 8 == 0:
                    row = ctk.CTkFrame(ef, fg_color="transparent")
                    row.pack(fill="x")
                var = ctk.BooleanVar(value=ext in current)
                self.ext_vars[ext] = (var, cat_var)
                cb = ctk.CTkCheckBox(row, text=ext, variable=var,
                                     font=ctk.CTkFont(size=11),
                                     text_color="#a0a5b4",
                                     border_color="#3a4055", checkmark_color="#11131c",
                                     fg_color="#8be9fd", hover_color="#6272a4",
                                     width=20, height=20, corner_radius=4,
                                     command=lambda e=ext, cv=cat_var, c=cat: self._sync(c, cv))
                cb.pack(side="left", padx=2, pady=1)

        # Bottom
        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.pack(fill="x", padx=20, pady=(8, 14))
        ctk.CTkButton(bf, text="取消", width=80, height=32, corner_radius=6,
                      fg_color="transparent", hover_color="#2a2f40",
                      text_color="#6272a4", font=ctk.CTkFont(size=12),
                      command=self.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(bf, text="保存设置", width=100, height=32, corner_radius=6,
                      fg_color="#8be9fd", hover_color="#a4f0ff",
                      text_color="#0d1117", font=ctk.CTkFont(size=12, weight="bold"),
                      command=self._save).pack(side="right")

    def _tgl_cat(self, cat, var):
        st = var.get()
        for e in VIDEO_EXTENSIONS.get(cat, []):
            if e in self.ext_vars: self.ext_vars[e][0].set(st)

    def _sync(self, cat, cat_var):
        exts = VIDEO_EXTENSIONS.get(cat, [])
        cat_var.set(all(self.ext_vars[e][0].get() for e in exts if e in self.ext_vars))

    def _sel_all(self):
        for v, _ in self.ext_vars.values(): v.set(True)

    def _sel_none(self):
        for v, _ in self.ext_vars.values(): v.set(False)

    def _sel_cats(self, cats):
        self._sel_none()
        for cat in cats:
            for e in VIDEO_EXTENSIONS.get(cat, []):
                if e in self.ext_vars: self.ext_vars[e][0].set(True)

    def _save(self):
        self.callback({e for e, (v, _) in self.ext_vars.items() if v.get()})
        self.destroy()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _reveal_in_explorer(path):
    if os.path.exists(path):
        if sys.platform == "win32":
            os.system(f'explorer /select,"{path}"')
        else:
            import subprocess; subprocess.run(["open", "-R", path])


if __name__ == "__main__":
    App().mainloop()
