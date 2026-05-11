#!/usr/bin/env python3
"""
CleanDup — PySide6 desktop client for duplicate file detection
原生表格渲染，虚拟滚动，流畅不卡。
"""

import os, sys, time, queue, threading, json, re
from datetime import datetime
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QProgressBar,
    QTabWidget, QTableView, QHeaderView, QSplitter, QStatusBar,
    QMenu, QMessageBox, QFileDialog, QAbstractItemView, QStyledItemDelegate,
    QFrame, QSizePolicy, QStyle, QStyleOptionButton,
)
from PySide6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, Signal, Slot, QTimer, QThread, QSize,
)
from PySide6.QtGui import QColor, QFont, QAction, QCursor, QPalette, QIcon

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
def _app_dir():
    """Persistent data directory — exe folder for PyInstaller, script folder for dev."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def _resource_path(relative_path):
    """Get path to bundled resource — works in PyInstaller and dev mode."""
    if getattr(sys, 'frozen', False):
        # PyInstaller extracts bundled files to sys._MEIPASS
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

SETTINGS_FILE = os.path.join(_app_dir(), "settings.json")

from config import DB_PATH, MIN_FILE_SIZE_MB
from database import Database
from deduplicator import run_dedup
from reporter import format_size


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                exts = set(json.load(f).get("extensions", []))
                if exts: return exts
        except: pass
    all_exts = set()
    for g in VIDEO_EXTENSIONS.values(): all_exts.update(g)
    return all_exts


def save_settings(extensions):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({"extensions": sorted(extensions)}, f, ensure_ascii=False, indent=2)


# ─── Table Models ────────────────────────────────────────────────────────────

class SearchTableModel(QAbstractTableModel):
    """Model for search results — only stores data, QTableView handles rendering."""
    def __init__(self):
        super().__init__()
        self._data = []  # [(path, size_human, dup_label), ...]
        self._headers = ["文件路径", "大小", "重复状态"]

    def rowCount(self, parent=QModelIndex()): return len(self._data)
    def columnCount(self, parent=QModelIndex()): return 3

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return None
        row, col = index.row(), index.column()
        if role == Qt.DisplayRole:
            return str(self._data[row][col])
        if role == Qt.ForegroundRole:
            if col == 2 and "个重复" in str(self._data[row][2]) and "0" not in str(self._data[row][2]):
                return QColor("#ff5555")
            return QColor("#c0c5d4")
        if role == Qt.UserRole:
            return self._data[row]
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self._headers[section]
        return None

    def set_data(self, data):
        self.beginResetModel()
        self._data = data
        self.endResetModel()

    def get_row_path(self, row):
        if 0 <= row < len(self._data):
            return self._data[row][0]
        return ""


class DupGroupModel(QAbstractTableModel):
    """Model for duplicate groups with batch selection support."""
    def __init__(self):
        super().__init__()
        self._groups = []      # [(hash, size, files_sorted), ...]
        self._checked = set()  # Set of checked row indices
        self._headers = ["", "#", "大小", "数量", "浪费", "保留文件"]

    def rowCount(self, parent=QModelIndex()): return len(self._groups)
    def columnCount(self, parent=QModelIndex()): return 6

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return None
        r, c = index.row(), index.column()
        ghash, fsize, files = self._groups[r]
        wasted = (len(files) - 1) * fsize
        keep_path = files[0][0]

        if role == Qt.CheckStateRole and c == 0:
            return Qt.Checked if r in self._checked else Qt.Unchecked
        if role == Qt.DisplayRole:
            if c == 0: return ""
            if c == 1: return str(r + 1)
            if c == 2: return format_size(fsize)
            if c == 3: return f"{len(files)}×"
            if c == 4: return format_size(wasted)
            if c == 5: return keep_path
        if role == Qt.ForegroundRole:
            if c == 2: return QColor("#8be9fd")
            if c == 1: return QColor("#6272a4")
            return QColor("#c0c5d4")
        if role == Qt.UserRole:
            return (r, ghash, fsize, files)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self._headers[section]
        return None

    def flags(self, index):
        if index.column() == 0:
            return Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def setData(self, index, value, role=Qt.CheckStateRole):
        if index.column() == 0:
            r = index.row()
            if value == Qt.Checked:
                self._checked.add(r)
            else:
                self._checked.discard(r)
            self.dataChanged.emit(index, index, [role])
            return True
        return False

    def set_groups(self, groups):
        self.beginResetModel()
        self._groups = groups
        self._checked.clear()
        self.endResetModel()

    def check_all(self):
        self._checked = set(range(len(self._groups)))
        self.beginResetModel(); self.endResetModel()

    def uncheck_all(self):
        self._checked.clear()
        self.beginResetModel(); self.endResetModel()

    def has_any_checked(self):
        return len(self._checked) > 0

    def get_checked_groups(self):
        return [self._groups[r] for r in sorted(self._checked)]

    def get_checked_count(self):
        return len(self._checked)


class FileDetailModel(QAbstractTableModel):
    """Model for individual files within a duplicate group."""
    def __init__(self):
        super().__init__()
        self._files = []  # [(path, size, mtime_ns, checked), ...]
        self._headers = ["", "文件路径", "大小", "修改时间"]

    def rowCount(self, parent=QModelIndex()): return len(self._files)
    def columnCount(self, parent=QModelIndex()): return 4

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return None
        r, c = index.row(), index.column()
        fp, fs, mt, checked = self._files[r]
        mtime_str = datetime.fromtimestamp(mt / 1e9).strftime("%Y-%m-%d %H:%M")

        if role == Qt.DisplayRole:
            if c == 0: return ""
            if c == 1: return fp
            if c == 2: return format_size(fs)
            if c == 3: return mtime_str
        if role == Qt.CheckStateRole and c == 0:
            return Qt.Checked if checked else Qt.Unchecked
        if role == Qt.ForegroundRole:
            if c == 0: return QColor("#ff5555")
            return QColor("#a0a5b4")
        if role == Qt.UserRole:
            return fp
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self._headers[section]
        return None

    def flags(self, index):
        if index.column() == 0:
            return Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def set_files(self, files):
        self.beginResetModel()
        # Default: check all except first (oldest = kept)
        self._files = [(fp, fs, mt, fi != 0) for fi, (fp, fs, mt) in enumerate(files)]
        self.endResetModel()

    def setData(self, index, value, role=Qt.CheckStateRole):
        if index.column() == 0:
            self._files[index.row()] = (*self._files[index.row()][:3], value == Qt.Checked)
            self.dataChanged.emit(index, index, [role])
            return True
        return False

    def get_checked(self):
        return [fp for fp, _, _, checked in self._files if checked]

    def get_unchecked(self):
        return [fp for fp, _, _, checked in self._files if not checked]

    def check_all(self):
        for i in range(len(self._files)):
            self._files[i] = (*self._files[i][:3], True)
        self.beginResetModel(); self.endResetModel()

    def uncheck_all(self):
        for i in range(len(self._files)):
            self._files[i] = (*self._files[i][:3], False)
        self.beginResetModel(); self.endResetModel()


# ─── Scan Worker ─────────────────────────────────────────────────────────────

class ScanWorker(QThread):
    progress = Signal(str, str)   # stage, message
    finished = Signal(list, int, int)  # dup_list, total_files, wasted_bytes
    error = Signal(str)

    def __init__(self, drives, force, extensions, fast_mode, min_mb):
        super().__init__()
        self.drives = drives
        self.force = force
        self.extensions = extensions
        self.fast_mode = fast_mode
        self.min_mb = min_mb

    def run(self):
        try:
            import config as cfg
            if self.min_mb > 0:
                cfg.MIN_FILE_SIZE = self.min_mb * MB

            db = Database()
            db.init_db()

            def on_progress(stage, msg):
                self.progress.emit(stage, msg)

            self.progress.emit("scan_start", "开始扫描...")
            groups = run_dedup(self.drives, db, force=self.force,
                progress_callback=on_progress, extensions=self.extensions,
                fast_mode=self.fast_mode)

            dup_list = []
            for fhash, fsize, files in groups:
                dup_list.append((fhash, fsize, sorted(files, key=lambda x: x[2])))
            dup_list.sort(key=lambda g: (len(g[2]) - 1) * g[1], reverse=True)

            total = len(db.existing_paths_map())
            wasted = sum((len(g[2]) - 1) * g[1] for g in dup_list)
            self.finished.emit(dup_list, total, wasted)
            db.close()
        except Exception as e:
            self.error.emit(str(e))


# ─── Settings Dialog ─────────────────────────────────────────────────────────

class SettingsDialog(QWidget):
    def __init__(self, current_exts, parent=None):
        super().__init__(parent, Qt.Window | Qt.Dialog)
        self.setWindowTitle("文件后缀设置")
        self.setMinimumSize(500, 400)
        self.ext_vars = {}
        self.saved = False
        self.result = current_exts

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("选择要扫描的文件后缀（未勾选的会被跳过）"))

        # Quick buttons
        qa = QHBoxLayout()
        for txt, fn in [("全选", lambda: self._sel(True)), ("取消全选", lambda: self._sel(False))]:
            btn = QPushButton(txt)
            btn.clicked.connect(fn)
            qa.addWidget(btn)
        layout.addLayout(qa)

        # Categories with checkboxes
        for cat, exts in VIDEO_EXTENSIONS.items():
            cat_cb = QCheckBox(cat)
            cat_cb.setChecked(all(e in current_exts for e in exts))
            cat_cb.toggled.connect(lambda checked, c=cat: self._tgl_cat(c, checked))
            layout.addWidget(cat_cb)

            row_layout = QHBoxLayout()
            for ext in exts:
                cb = QCheckBox(ext)
                cb.setChecked(ext in current_exts)
                cb.toggled.connect(lambda checked, c=cat: self._sync_cat(c))
                self.ext_vars[ext] = cb
                row_layout.addWidget(cb)
            layout.addLayout(row_layout)

        # Save button
        btn = QPushButton("保存设置")
        btn.clicked.connect(self._save)
        layout.addWidget(btn)

    def _tgl_cat(self, cat, checked):
        for e in VIDEO_EXTENSIONS.get(cat, []):
            if e in self.ext_vars:
                self.ext_vars[e].setChecked(checked)

    def _sync_cat(self, cat):
        pass  # Simplified; could update category checkbox state

    def _sel(self, state):
        for cb in self.ext_vars.values():
            cb.setChecked(state)

    def _save(self):
        self.result = {e for e, cb in self.ext_vars.items() if cb.isChecked()}
        self.saved = True
        self.close()


# ─── Main Window ─────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CleanDup")
        self.resize(1150, 720)
        self.setMinimumSize(900, 500)

        # Set icon
        icon_path = _resource_path("app_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # Dark palette
        self._set_dark_theme()

        # State
        self.scan_running = False
        self.active_exts = load_settings()
        self.db = Database()
        self.progress_queue = queue.Queue()
        self.dup_groups = []
        self.expanded_row = -1  # Currently expanded group index

        # Scan worker placeholder
        self.scan_worker = None

        self._build_ui()
        self._load_data()
        self._start_auto_scan()

        # Poll progress
        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._poll_progress)
        self._poll_timer.start(200)

    def _set_dark_theme(self):
        p = self.palette()
        p.setColor(QPalette.Window, QColor("#0f1419"))
        p.setColor(QPalette.WindowText, QColor("#c0c5d4"))
        p.setColor(QPalette.Base, QColor("#1a1d29"))
        p.setColor(QPalette.AlternateBase, QColor("#1e2233"))
        p.setColor(QPalette.Text, QColor("#c0c5d4"))
        p.setColor(QPalette.Button, QColor("#1e2240"))
        p.setColor(QPalette.ButtonText, QColor("#8be9fd"))
        p.setColor(QPalette.Highlight, QColor("#3b82f6"))
        p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        self.setPalette(p)

    # ── Build UI ─────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(12, 10, 12, 8)

        # ── Search bar ──
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("🔍"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索文件名... 留空显示全部")
        self.search_input.textChanged.connect(self._on_search)
        search_row.addWidget(self.search_input)
        self.search_count = QLabel("")
        search_row.addWidget(self.search_count)
        main_layout.addLayout(search_row)

        # ── Controls ──
        ctrl_row = QHBoxLayout()

        # Drives
        ctrl_row.addWidget(QLabel("盘符:"))
        self.drive_checks = {}
        import string
        for d in string.ascii_uppercase:
            if os.path.exists(f"{d}:\\"):
                cb = QCheckBox(f"{d}:")
                cb.setChecked(d != "C")
                self.drive_checks[d] = cb
                ctrl_row.addWidget(cb)

        ctrl_row.addSpacing(12)

        # Full hash
        self.full_hash_cb = QCheckBox("全量哈希（慢）")
        ctrl_row.addWidget(self.full_hash_cb)

        # Min size
        ctrl_row.addWidget(QLabel("最小(MB):"))
        self.min_size_input = QLineEdit(str(MIN_FILE_SIZE_MB))
        self.min_size_input.setFixedWidth(50)
        ctrl_row.addWidget(self.min_size_input)

        ctrl_row.addStretch()
        main_layout.addLayout(ctrl_row)

        # ── Buttons ──
        btn_row = QHBoxLayout()

        self.status_lbl = QLabel("● 准备中...")
        self.status_lbl.setStyleSheet("color: #f59e0b; font-weight: bold;")
        btn_row.addWidget(self.status_lbl)

        self.incr_btn = QPushButton("↻ 增量更新")
        self.incr_btn.clicked.connect(lambda: self._run_scan(False))
        btn_row.addWidget(self.incr_btn)

        self.refresh_btn = QPushButton("⟳ 全量刷新")
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        btn_row.addWidget(self.refresh_btn)

        btn_row.addStretch()

        for txt, slot in [("设置", self._open_settings), ("清理DB", self._clean_db),
                           ("导出CSV", self._export_csv), ("导出JSON", self._export_json)]:
            btn = QPushButton(txt)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)

        main_layout.addLayout(btn_row)

        # ── Progress ──
        self.prog_bar = QProgressBar()
        self.prog_bar.setMaximum(10000)  # 0-100.00%
        self.prog_bar.setFixedHeight(4)
        self.prog_bar.setTextVisible(False)
        main_layout.addWidget(self.prog_bar)
        self.prog_text = QLabel("")
        self.prog_text.setStyleSheet("color: #6272a4; font-size: 11px;")
        main_layout.addWidget(self.prog_text)

        # ── Tabs ──
        self.tabs = QTabWidget()

        # Tab 1: Duplicate groups
        dup_widget = QWidget()
        dup_layout = QVBoxLayout(dup_widget)
        dup_layout.setContentsMargins(0, 0, 0, 0)

        # Batch action bar
        batch_bar = QHBoxLayout()
        self.batch_toggle_btn = QPushButton("全选")
        self.batch_toggle_btn.clicked.connect(self._batch_toggle)
        batch_bar.addWidget(self.batch_toggle_btn)
        self.batch_delete_btn = QPushButton("🗑 批量清理勾选的组")
        self.batch_delete_btn.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold; padding: 4px 12px;")
        self.batch_delete_btn.clicked.connect(self._batch_delete)
        self.batch_delete_btn.setEnabled(False)
        batch_bar.addWidget(self.batch_delete_btn)
        batch_bar.addStretch()
        self.batch_info_lbl = QLabel("")
        self.batch_info_lbl.setStyleSheet("color: #6272a4;")
        batch_bar.addWidget(self.batch_info_lbl)
        dup_layout.addLayout(batch_bar)

        splitter = QSplitter(Qt.Vertical)

        # Top: group list
        self.dup_table = QTableView()
        self.dup_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.dup_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.dup_table.setDragEnabled(True)
        self.dup_table.setDragDropMode(QAbstractItemView.NoDragDrop)
        self.dup_table.horizontalHeader().setStretchLastSection(True)
        self.dup_table.verticalHeader().setVisible(False)
        self.dup_table.setShowGrid(False)
        self.dup_table.setAlternatingRowColors(True)
        self.dup_table.clicked.connect(self._on_dup_table_clicked)
        self.dup_model = DupGroupModel()
        self.dup_model.dataChanged.connect(self._update_batch_ui)
        self.dup_table.setModel(self.dup_model)
        splitter.addWidget(self.dup_table)

        # Bottom: file details
        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)

        detail_header = QHBoxLayout()
        detail_header.addWidget(QLabel("勾选 = 删除"))
        detail_header.addStretch()
        self.detail_toggle_btn = QPushButton("全选")
        self.detail_toggle_btn.clicked.connect(self._detail_toggle_all)
        detail_header.addWidget(self.detail_toggle_btn)
        self.detail_delete_btn = QPushButton("🗑 删除勾选的文件")
        self.detail_delete_btn.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold;")
        self.detail_delete_btn.clicked.connect(self._delete_checked)
        detail_header.addWidget(self.detail_delete_btn)
        detail_layout.addLayout(detail_header)

        self.detail_table = QTableView()
        self.detail_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.detail_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.detail_table.horizontalHeader().setStretchLastSection(True)
        self.detail_table.verticalHeader().setVisible(False)
        self.detail_table.setShowGrid(False)
        self.detail_table.setAlternatingRowColors(True)
        self.detail_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.detail_table.customContextMenuRequested.connect(self._detail_context_menu)
        self.detail_model = FileDetailModel()
        self.detail_model.dataChanged.connect(self._update_toggle_btn)
        self.detail_table.setModel(self.detail_model)
        self.detail_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        # Direct click handler for checkbox toggling (Qt model setData is unreliable for checkboxes)
        self.detail_table.clicked.connect(self._on_detail_checkbox_clicked)
        detail_layout.addWidget(self.detail_table)

        detail_widget.setVisible(False)
        splitter.addWidget(detail_widget)
        self.detail_widget = detail_widget

        dup_layout.addWidget(splitter)
        self.tabs.addTab(dup_widget, "重复组")

        # Tab 2: Search results
        search_widget = QWidget()
        search_layout = QVBoxLayout(search_widget)
        search_layout.setContentsMargins(0, 0, 0, 0)

        self.search_table = QTableView()
        self.search_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.search_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.search_table.horizontalHeader().setStretchLastSection(True)
        self.search_table.verticalHeader().setVisible(False)
        self.search_table.setShowGrid(False)
        self.search_table.setAlternatingRowColors(True)
        self.search_table.doubleClicked.connect(self._reveal_search_file)
        self.search_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.search_table.customContextMenuRequested.connect(self._search_context_menu)
        self.search_model = SearchTableModel()
        self.search_table.setModel(self.search_model)
        self.search_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        search_layout.addWidget(self.search_table)

        self.tabs.addTab(search_widget, "搜索结果")
        main_layout.addWidget(self.tabs)

        # ── Status bar ──
        self.sbar = QStatusBar()
        self.sbar.setStyleSheet("color: #5a6478;")
        self.setStatusBar(self.sbar)
        # Drive capacity widget on the right side of status bar
        self.drive_capacity_lbl = QLabel("")
        self.drive_capacity_lbl.setStyleSheet("color: #8be9fd; padding-right: 8px;")
        self.sbar.addPermanentWidget(self.drive_capacity_lbl)
        # Keyboard navigation for dup table
        self.dup_table.selectionModel().currentChanged.connect(self._on_dup_current_changed)
        # Update drive capacity on startup, when drives toggled, and every 30s
        self._update_drive_capacity()
        for cb in self.drive_checks.values():
            cb.toggled.connect(self._update_drive_capacity)
        self._cap_timer = QTimer()
        self._cap_timer.timeout.connect(self._update_drive_capacity)
        self._cap_timer.start(30000)  # 30s

    # ── Drive capacity ───────────────────────────────────────────────────

    def _update_drive_capacity(self):
        """Show total/free space for selected drives (system call, no scan)."""
        import shutil
        drives = [f"{d}:\\" for d, cb in self.drive_checks.items() if cb.isChecked()]
        total_all = 0
        free_all = 0
        for drive in drives:
            try:
                usage = shutil.disk_usage(drive)
                total_all += usage.total
                free_all += usage.free
            except OSError:
                pass
        if total_all > 0:
            self.drive_capacity_lbl.setText(
                f"已选 {len(drives)} 盘  |  总容量 {format_size(total_all, prec=4)}  |  可用 {format_size(free_all, prec=4)}")
        else:
            self.drive_capacity_lbl.setText("")

    # ── Data loading ─────────────────────────────────────────────────────

    def _load_data(self):
        def _load():
            try:
                db = Database()
                stats = db.get_stats()
                total = stats["total_files"]
                dupc = stats["duplicate_groups"]
                wasted = format_size(stats["wasted_bytes"])
                self.sbar.showMessage(f"已索引 {total:,} 个文件  ·  {dupc:,} 组重复  ·  可释放 {wasted}")

                # Search: first 200
                rows = db.search_files("", limit=200) if total > 0 else []
                data = [(fp, format_size(fs), f"{dc}个重复" if dc > 0 else "唯一")
                        for fp, fs, _, _, _, dc in rows]
                self.search_model.set_data(data)
                self.search_count.setText(f"显示前 {len(data)} / {total:,}")

                # Dup groups: first 200
                groups = db.get_duplicate_groups()
                dup_list = []
                for fhash, fsize, files in groups:
                    dup_list.append((fhash, fsize, sorted(files, key=lambda x: x[2])))
                dup_list.sort(key=lambda g: (len(g[2]) - 1) * g[1], reverse=True)
                self.dup_model.set_groups(dup_list[:200])
                self.dup_groups = dup_list
                db.close()
            except Exception:
                pass
        threading.Thread(target=_load, daemon=True).start()

    # ── Search ───────────────────────────────────────────────────────────

    def _on_search(self, text):
        q = text.strip()
        def _do():
            rows = self.db.search_files("", limit=5000) if len(q) < 2 else self.db.search_files(q, limit=200)
            label = f"全部 {len(rows):,} 个文件" if len(q) < 2 else f"找到 {len(rows)} 个"
            data = [(fp, format_size(fs), f"{dc}个重复" if dc > 0 else "唯一")
                    for fp, fs, _, _, _, dc in rows]
            self.search_model.set_data(data)
            self.search_count.setText(label)
        threading.Thread(target=_do, daemon=True).start()

    # ── Scan ─────────────────────────────────────────────────────────────

    def _start_auto_scan(self):
        # Clean stale records then incremental scan — skips unchanged files
        QTimer.singleShot(300, self._clean_db_silent)
        QTimer.singleShot(600, lambda: self._run_scan(False))

    def _clean_db_silent(self):
        """Clean missing files without confirmation dialog (runs in background)."""
        def _run():
            try:
                db = Database()
                db.init_db()
                n = db.remove_nonexistent()
                db.close()
                if n > 0:
                    self.sbar.showMessage(f"清理了 {n} 条失效记录")
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

    def _on_refresh_clicked(self):
        reply = QMessageBox.question(self, "确认", "全量刷新将重新扫描所有文件。确定？")
        if reply == QMessageBox.Yes:
            self._run_scan(True)

    def _run_scan(self, force):
        if self.scan_running: return
        drives = [f"{d}:\\" for d, cb in self.drive_checks.items() if cb.isChecked()]
        if not drives:
            QTimer.singleShot(500, lambda: self._run_scan(force))
            return

        self.scan_running = True
        self.status_lbl.setText("● 扫描中...")
        self.status_lbl.setStyleSheet("color: #f59e0b; font-weight: bold;")
        self.incr_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.prog_bar.setValue(0)

        exts = self.active_exts if self.active_exts else None
        try:
            min_mb = int(self.min_size_input.text().strip() or "0")
        except ValueError:
            min_mb = 200

        self.scan_worker = ScanWorker(drives, force, exts,
                                      not self.full_hash_cb.isChecked(), min_mb)
        self.scan_worker.progress.connect(self._on_scan_progress)
        self.scan_worker.finished.connect(self._on_scan_done)
        self.scan_worker.error.connect(self._on_scan_error)
        self.scan_worker.start()

    @Slot(str, str)
    def _on_scan_progress(self, stage, msg):
        pct = self._calc_pct(stage, msg)
        self.prog_bar.setValue(int(pct * 100))
        self.prog_text.setText(f"{pct:.2f}%  {msg}")

    @Slot(list, int, int)
    def _on_scan_done(self, dup_list, total, wasted):
        self.scan_running = False
        self.prog_bar.setValue(10000)
        self.prog_text.setText(f"100.00%  扫描完成  ·  {total:,} 文件  ·  {len(dup_list):,} 组重复  ·  可释放 {format_size(wasted)}")
        self.status_lbl.setText("✓ 已是最新")
        self.status_lbl.setStyleSheet("color: #22c55e; font-weight: bold;")
        self.incr_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.dup_groups = dup_list
        self.dup_model.set_groups(dup_list[:200])
        self._update_drive_capacity()
        self._load_data()

    @Slot(str)
    def _on_scan_error(self, err):
        self.scan_running = False
        self.prog_text.setText(f"错误: {err}")
        self.status_lbl.setText("✗ 扫描出错")
        self.status_lbl.setStyleSheet("color: #ff5555; font-weight: bold;")
        self.incr_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)

    def _calc_pct(self, stage, msg):
        fast = not self.full_hash_cb.isChecked()
        if stage == "pass1_done": return 35.0 if fast else 25.0
        if stage == "pass2_done": return 100.0 if fast else 70.0
        if stage == "pass3_done": return 100.0
        m = re.search(r"(\d+)\s*/\s*(\d+)", msg)
        if not m: return 5.0
        cur, tot = int(m.group(1)), int(m.group(2))
        if tot == 0: return 50.0
        if "pass1" in stage or "scan" in stage.lower():
            base, weight = 0, 35 if fast else 25
        elif "pass2" in stage or "quick" in stage.lower():
            base, weight = 35 if fast else 25, 65 if fast else 45
        elif "pass3" in stage or "full" in stage.lower():
            base, weight = 70, 30
        else:
            base, weight = 30, 40
        return min(base + (cur / tot) * weight, 99.99)

    # ── Dup group interaction ────────────────────────────────────────────

    @Slot(QModelIndex)
    # ── Batch operations ─────────────────────────────────────────────────

    def _batch_toggle(self):
        if self.dup_model.has_any_checked():
            self.dup_model.uncheck_all()
        else:
            self.dup_model.check_all()
        self._update_batch_ui()

    def _update_batch_ui(self):
        n = self.dup_model.get_checked_count()
        all_checked = n == len(self.dup_model._groups) and n > 0
        self.batch_toggle_btn.setText("取消全选" if all_checked else "全选")
        self.batch_delete_btn.setEnabled(n > 0)
        if n > 0:
            total_wasted = sum(
                (len(g[2]) - 1) * g[1] for g in self.dup_model.get_checked_groups()
            )
            total_files = sum(len(g[2]) - 1 for g in self.dup_model.get_checked_groups())
            self.batch_info_lbl.setText(f"已选 {n} 组  |  可删除 {total_files} 个文件  |  释放 {format_size(total_wasted)}")
        else:
            self.batch_info_lbl.setText("")

    def _batch_delete(self):
        groups = self.dup_model.get_checked_groups()
        if not groups: return
        total_files = sum(len(g[2]) - 1 for g in groups)
        total_wasted = sum((len(g[2]) - 1) * g[1] for g in groups)

        reply = QMessageBox.question(self, "批量清理确认",
            f"将处理 {len(groups)} 组重复\n"
            f"每组保留 1 个文件（最旧），共删除 {total_files} 个文件\n"
            f"预计释放 {format_size(total_wasted)}\n\n确定？")
        if reply != QMessageBox.Yes: return

        deleted = 0
        errors = 0
        for ghash, fsize, files in groups:
            files_sorted = sorted(files, key=lambda x: x[2])  # oldest first
            for fp, fs, mt in files_sorted[1:]:  # delete all except first
                try:
                    if os.path.exists(fp): os.remove(fp)
                    db = Database()
                    db.conn.execute("DELETE FROM file_index WHERE file_path=?", (fp,))
                    db.conn.commit(); db.close()
                    deleted += 1
                except Exception:
                    errors += 1

        saved = format_size(total_wasted) if errors == 0 else "部分"
        self.sbar.showMessage(f"批量清理完成: 删除 {deleted} 个文件，释放约 {saved}")
        self._load_data()
        self.detail_widget.setVisible(False)

    def _on_dup_current_changed(self, current, previous):
        """Keyboard navigation: update detail panel when selection moves."""
        if not current.isValid(): return
        row = current.row()
        if 0 <= row < len(self.dup_groups):
            ghash, fsize, files = self.dup_groups[row]
            self.detail_model.set_files(files)
            self.detail_widget.setVisible(True)
            self._update_toggle_btn()
            self._current_dup_row = row
            self._current_dup_files = files

    def _on_dup_table_clicked(self, index):
        """Handle clicks: column 0 = toggle checkbox, others = expand group."""
        if index.column() == 0:
            # Toggle checkbox directly (Qt setData unreliable for this)
            r = index.row()
            if r in self.dup_model._checked:
                self.dup_model._checked.discard(r)
            else:
                self.dup_model._checked.add(r)
            self.dup_model.dataChanged.emit(index, index, [Qt.CheckStateRole])
            self._update_batch_ui()
            return

        # Expand group detail
        row = index.row()
        if 0 <= row < len(self.dup_groups):
            ghash, fsize, files = self.dup_groups[row]
            self.detail_model.set_files(files)
            self.detail_widget.setVisible(True)
            self._update_toggle_btn()
            self._current_dup_row = row
            self._current_dup_files = files

    def _reveal_dup_file(self, index):
        row = index.row()
        if 0 <= row < len(self.dup_groups):
            _reveal_in_explorer(self.dup_groups[row][2][0][0])

    def _on_detail_checkbox_clicked(self, index):
        """Toggle checkbox for column 0 clicks."""
        if index.column() != 0:
            return
        # Toggle the checked state
        fp, fs, mt, checked = self.detail_model._files[index.row()]
        new_state = not checked
        self.detail_model._files[index.row()] = (fp, fs, mt, new_state)
        self.detail_model.dataChanged.emit(index, index, [Qt.CheckStateRole])

    def _detail_context_menu(self, pos):
        """Right-click menu for files in detail view (supports multi-select)."""
        index = self.detail_table.indexAt(pos)
        if index.isValid() and not self.detail_table.selectionModel().selectedRows():
            self.detail_table.selectRow(index.row())
        paths = self._get_selected_paths(self.detail_table, self.detail_model)
        if not paths: return
        count = len(paths)
        label = f" ({count}个)" if count > 1 else ""

        menu = QMenu(self)
        menu.addAction(f"📂 打开文件位置{label}",
                       lambda: [_reveal_in_explorer(p) for p in paths])
        menu.addAction(f"📋 复制路径{label}",
                       lambda: (QApplication.clipboard().setText("\n".join(paths)),
                                self.sbar.showMessage(f"已复制 {count} 个路径")))
        menu.addSeparator()
        menu.addAction(f"🗑 删除此文件{label}",
                       lambda: [self._delete_one_file(p) for p in paths])
        menu.exec(QCursor.pos())

    def _detail_toggle_all(self):
        all_checked = all(c for _, _, _, c in self.detail_model._files)
        if all_checked:
            self.detail_model.uncheck_all()
        else:
            self.detail_model.check_all()
        self._update_toggle_btn()

    def _update_toggle_btn(self):
        all_checked = all(c for _, _, _, c in self.detail_model._files)
        self.detail_toggle_btn.setText("取消全选" if all_checked else "全选")

    def _delete_checked(self):
        checked = self.detail_model.get_checked()
        unchecked = self.detail_model.get_unchecked()
        if not checked:
            QMessageBox.warning(self, "提示", "请勾选要删除的文件")
            return
        if not unchecked:
            QMessageBox.warning(self, "提示", "至少保留一个文件")
            return

        preview = "\n".join(p[:100] for p in checked[:5])
        if len(checked) > 5: preview += f"\n... 等共 {len(checked)} 个"

        reply = QMessageBox.question(self, "确认删除",
            f"将删除 {len(checked)} 个文件\n\n{preview}")
        if reply != QMessageBox.Yes: return

        deleted = 0
        for fp in checked:
            try:
                if os.path.exists(fp): os.remove(fp)
                db = Database()
                db.conn.execute("DELETE FROM file_index WHERE file_path=?", (fp,))
                db.conn.commit(); db.close()
                deleted += 1
            except Exception as e:
                QMessageBox.critical(self, "错误", f"删除失败: {e}")

        self.detail_widget.setVisible(False)
        self.sbar.showMessage(f"已删除 {deleted} 个重复文件")
        self._load_data()

    # ── Search table ─────────────────────────────────────────────────────

    def _reveal_search_file(self, index):
        fp = self.search_model.get_row_path(index.row())
        if fp: _reveal_in_explorer(fp)

    def _search_context_menu(self, pos):
        # Auto-select the row under cursor if nothing selected
        index = self.search_table.indexAt(pos)
        if index.isValid() and not self.search_table.selectionModel().selectedRows():
            self.search_table.selectRow(index.row())
        paths = self._get_selected_paths(self.search_table, self.search_model)
        if not paths: return
        count = len(paths)
        label = f" ({count}个)" if count > 1 else ""

        menu = QMenu(self)
        menu.addAction(f"📂 打开文件位置{label}",
                       lambda: [_reveal_in_explorer(p) for p in paths])
        menu.addAction(f"📋 复制路径{label}",
                       lambda: (QApplication.clipboard().setText("\n".join(paths)),
                                self.sbar.showMessage(f"已复制 {count} 个路径")))
        menu.addSeparator()
        menu.addAction(f"🗑 删除此文件{label}",
                       lambda: [self._delete_one_file(p) for p in paths])
        menu.exec(QCursor.pos())

    def _get_selected_paths(self, table, model):
        """Get all selected file paths from a table (supports multi-select)."""
        paths = []
        for index in table.selectionModel().selectedRows():
            if index.isValid():
                fp = model.data(index, Qt.UserRole)
                if fp: paths.append(fp)
        return paths

    def _delete_one_file(self, fp):
        reply = QMessageBox.question(self, "确认删除", f"确定删除此文件？\n\n{fp}")
        if reply != QMessageBox.Yes: return
        try:
            if os.path.exists(fp): os.remove(fp)
            db = Database()
            db.conn.execute("DELETE FROM file_index WHERE file_path=?", (fp,))
            db.conn.commit(); db.close()
            self.sbar.showMessage(f"已删除: {os.path.basename(fp)}")
            self._update_drive_capacity()

            # Immediately update detail model — remove the deleted file
            new_files = [(p, s, m, c) for p, s, m, c in self.detail_model._files if p != fp]
            if len(new_files) <= 1:
                # No longer a duplicate group — remove from dup list entirely
                self.detail_widget.setVisible(False)
                if hasattr(self, '_current_dup_row'):
                    self.dup_groups.pop(self._current_dup_row)
                self.dup_model.set_groups(self.dup_groups[:200])
            else:
                # Update the files in the current dup group
                self.detail_model._files = new_files
                self.detail_model.beginResetModel()
                self.detail_model.endResetModel()
                # Update dup_groups and dup_model
                if hasattr(self, '_current_dup_row') and hasattr(self, '_current_dup_files'):
                    old_files = self._current_dup_files
                    remaining = [(p, s, m) for p, s, m in old_files if p != fp]
                    if remaining:
                        ghash, fsize, _ = self.dup_groups[self._current_dup_row]
                        self.dup_groups[self._current_dup_row] = (ghash, fsize, remaining)
                        self.dup_model.set_groups(self.dup_groups[:200])

            # Background reload to sync stats
            self._load_data()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"删除失败: {e}")

    # ── Settings ──────────────────────────────────────────────────────────

    def _open_settings(self):
        dlg = SettingsDialog(self.active_exts, self)
        dlg.show()
        # Hack: wait for dialog close
        while dlg.isVisible():
            QApplication.processEvents()
        if dlg.saved:
            self.active_exts = dlg.result
            save_settings(dlg.result)
            self.sbar.showMessage(f"设置已保存 ({len(dlg.result)} 个后缀)")

    # ── Export ────────────────────────────────────────────────────────────

    def _export_csv(self):
        from reporter import export_csv
        fn, _ = QFileDialog.getSaveFileName(self, "导出CSV",
            f"dupes-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv", "CSV (*.csv)")
        if fn:
            db = Database(); g = db.get_duplicate_groups()
            export_csv(g, fn, db); db.close()
            self.sbar.showMessage(f"已导出: {os.path.basename(fn)}")

    def _export_json(self):
        from reporter import export_json
        fn, _ = QFileDialog.getSaveFileName(self, "导出JSON",
            f"dupes-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json", "JSON (*.json)")
        if fn:
            db = Database(); g = db.get_duplicate_groups()
            export_json(g, fn, db); db.close()
            self.sbar.showMessage(f"已导出: {os.path.basename(fn)}")

    # ── Clean ─────────────────────────────────────────────────────────────

    def _clean_db(self):
        reply = QMessageBox.question(self, "确认", "清理数据库中已不存在的文件记录？")
        if reply == QMessageBox.Yes:
            db = Database(); db.init_db()
            n = db.remove_nonexistent(); db.close()
            self.sbar.showMessage(f"已清理 {n} 条记录")
            self._load_data()

    # ── Progress polling ─────────────────────────────────────────────────

    def _poll_progress(self):
        try:
            while True:
                msg = self.progress_queue.get_nowait()
                if msg[0] == "progress":
                    _, stage, text = msg
                    pct = self._calc_pct(stage, text)
                    self.prog_bar.setValue(int(pct * 100))
                    self.prog_text.setText(f"{pct:.2f}%  {text}")
                elif msg[0] == "done":
                    _, dup_list, total, wasted = msg
                    self.prog_bar.setValue(10000)
                    self.prog_text.setText(f"100.00%  扫描完成")
                    self.status_lbl.setText("✓ 已是最新")
                    self.incr_btn.setEnabled(True)
                    self.refresh_btn.setEnabled(True)
                    self.dup_groups = dup_list
                    self.dup_model.set_groups(dup_list[:200])
                    self._load_data()
                elif msg[0] == "error":
                    self.prog_text.setText(f"错误: {msg[1]}")
                    self.incr_btn.setEnabled(True)
                    self.refresh_btn.setEnabled(True)
        except queue.Empty:
            pass


def _reveal_in_explorer(path):
    if os.path.exists(path):
        if sys.platform == "win32":
            import subprocess
            subprocess.Popen(['explorer', '/select,', path])


if __name__ == "__main__":
    # Separate taskbar icon from python.exe on Windows
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CleanDup")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # Consistent dark look across platforms
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
