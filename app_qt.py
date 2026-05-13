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
    QScrollArea,
    QTabWidget, QTableView, QHeaderView, QSplitter, QStatusBar,
    QTreeView,
    QMenu, QMessageBox, QFileDialog, QAbstractItemView, QStyledItemDelegate,
    QFrame, QSizePolicy,
)
from PySide6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, Signal, Slot, QTimer, QThread, QSize,
)
from PySide6.QtGui import QColor, QFont, QAction, QCursor, QPalette, QIcon, QStandardItemModel, QStandardItem, QCloseEvent
from PySide6.QtWidgets import QSystemTrayIcon

from send2trash import send2trash

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
    "其他视频": [".m4p", ".m4b", ".cpi", ".clpi"],
}
IMAGE_EXTENSIONS = {
    "常见图片": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".svg"],
    "RAW/专业": [".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".pef", ".raf",
                  ".sr2", ".srf", ".dcr", ".kdc", ".mrw", ".erf", ".3fr", ".fff", ".mef", ".mdc"],
    "其他图片": [".ico", ".heic", ".heif", ".psd", ".ai", ".eps", ".cdr", ".xcf"],
}
AUDIO_EXTENSIONS = {
    "常见音频": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus"],
    "无损/专业": [".alac", ".ape", ".aiff", ".dsf", ".dff", ".pcm"],
    "其他音频": [".mid", ".midi", ".amr", ".ac3", ".dts", ".ra"],
}
DOC_EXTENSIONS = {
    "文档": [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv", ".md"],
    "压缩包": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"],
    "代码/配置": [".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".yaml", ".ini", ".cfg"],
}
# Category hierarchy for settings UI
EXTENSION_CATEGORIES = {
    "视频": VIDEO_EXTENSIONS,
    "图片": IMAGE_EXTENSIONS,
    "音频": AUDIO_EXTENSIONS,
    "文档/其他": DOC_EXTENSIONS,
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
from scanner import walk_files
from hasher import quick_hash
from reporter import format_size


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                s = json.load(f)
                if s.get("scan_all", False):
                    return None  # None = scan all files
                exts = set(s.get("extensions", []))
                if exts: return exts
        except: pass
    # Default: all video extensions
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

    def sort(self, column, order=Qt.AscendingOrder):
        """Sort groups by column. 0=checkbox, 1=#, 2=size, 3=count, 4=wasted, 5=path."""
        key_map = {
            2: lambda g: g[1],       # size
            3: lambda g: len(g[2]),  # count
            4: lambda g: (len(g[2]) - 1) * g[1],  # wasted
            5: lambda g: g[2][0][0].lower(),       # path
        }
        key_fn = key_map.get(column, lambda g: (len(g[2]) - 1) * g[1])
        self._checked.clear()
        self.beginResetModel()
        self._groups.sort(key=key_fn, reverse=(order == Qt.DescendingOrder))
        self.endResetModel()


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
    finished = Signal(list, object, object)  # dup_list, total_files, wasted_bytes (object for large ints)
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
            import scanner
            scanner.MIN_FILE_SIZE = max(1, self.min_mb * MB)

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


# ─── Folder Compare ──────────────────────────────────────────────────────────
# Compares two folders by quick-hash, finds files existing in both.
# Delete buttons remove all duplicates from one side or the other.

class CompareResultModel(QAbstractTableModel):
    """Model for folder comparison results. Columns: size, path_a, path_b, actions."""
    def __init__(self):
        super().__init__()
        self._pairs = []  # [(hash, size, path_a, path_b), ...]
        self._headers = ["大小", "路径 (A)", "路径 (B)", "", ""]

    def rowCount(self, parent=QModelIndex()): return len(self._pairs)
    def columnCount(self, parent=QModelIndex()): return 5

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return None
        r, c = index.row(), index.column()
        fhash, fsize, pa, pb = self._pairs[r]
        if role == Qt.DisplayRole:
            if c == 0: return format_size(fsize)
            if c == 1: return pa
            if c == 2: return pb
            if c == 3: return "删A ←"
            if c == 4: return "→ 删B"
        if role == Qt.ForegroundRole:
            if c in (3,): return QColor("#ff5555")
            if c in (4,): return QColor("#ff9e64")
            if c == 0: return QColor("#8be9fd")
            return QColor("#c0c5d4")
        if role == Qt.UserRole:
            return (pa, pb)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self._headers[section]
        return None

    def set_pairs(self, pairs):
        self.beginResetModel()
        self._pairs = pairs
        self.endResetModel()

    def get_checked_paths(self, side):
        """Get all paths from the specified side ('A' or 'B')."""
        idx = 1 if side == "A" else 2
        return [p[idx] for p in self._pairs]


# ─── Local Dedup Check ───────────────────────────────────────────────────────
# Given a folder, finds files within it that have duplicates elsewhere in the DB.
# Two modes: delete local copies (keep others) or keep local (delete others).

class LocalCheckModel(QAbstractTableModel):
    """Model for local folder dedup check. Columns: size, local path, other paths, action."""
    def __init__(self):
        super().__init__()
        self._rows = []  # [(local_path, size, dup_paths_list), ...]
        self._headers = ["大小", "本文件夹内", "其他位置重复", ""]

    def rowCount(self, parent=QModelIndex()): return len(self._rows)
    def columnCount(self, parent=QModelIndex()): return 4

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return None
        r, c = index.row(), index.column()
        local_path, fsize, dup_paths = self._rows[r]
        if role == Qt.DisplayRole:
            if c == 0: return format_size(fsize)
            if c == 1: return local_path
            if c == 2: return "\n".join(dup_paths[:3]) + (f"\n... 等 {len(dup_paths)} 个" if len(dup_paths) > 3 else "")
            if c == 3: return "删除本地"
        if role == Qt.ForegroundRole:
            if c in (3,): return QColor("#ff5555")
            if c == 0: return QColor("#8be9fd")
            return QColor("#c0c5d4")
        if role == Qt.UserRole:
            return local_path
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self._headers[section]
        return None

    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def get_all_local_paths(self):
        return [r[0] for r in self._rows]


# ─── Empty Files Cleanup ─────────────────────────────────────────────────────
# Scans selected drives for 0-byte files and empty folders. Delete to recycle bin.

class EmptyFilesModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self._items = []  # [(type_str, path, size), ...]  type: "空文件" / "空文件夹"
        self._checked = set()
        self._headers = ["", "类型", "路径", "大小"]

    def rowCount(self, parent=QModelIndex()): return len(self._items)
    def columnCount(self, parent=QModelIndex()): return 4

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return None
        r, c = index.row(), index.column()
        typ, path, size = self._items[r]
        if role == Qt.CheckStateRole and c == 0:
            return Qt.Checked if r in self._checked else Qt.Unchecked
        if role == Qt.DisplayRole:
            if c == 0: return ""
            if c == 1: return typ
            if c == 2: return path
            if c == 3: return format_size(size)
        if role == Qt.ForegroundRole:
            if c == 1: return QColor("#f59e0b") if "夹" in typ else QColor("#ff5555")
            return QColor("#c0c5d4")
        if role == Qt.UserRole:
            return path
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self._headers[section]
        return None

    def flags(self, index):
        if index.column() == 0:
            return Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def set_items(self, items):
        self.beginResetModel()
        self._items = items
        self._checked = set(range(len(items)))  # default: all checked
        self.endResetModel()

    def check_all(self):
        self._checked = set(range(len(self._items)))
        self.beginResetModel(); self.endResetModel()

    def uncheck_all(self):
        self._checked.clear()
        self.beginResetModel(); self.endResetModel()

    def get_checked(self):
        return [self._items[r] for r in sorted(self._checked)]

    def get_checked_count(self):
        return len(self._checked)


# ─── Extension Statistics ─────────────────────────────────────────────────────

# ─── Settings Dialog ─────────────────────────────────────────────────────────

class SettingsDialog(QWidget):
    def __init__(self, current_exts, full_hash_enabled, min_size_mb, scan_all, parent=None):
        super().__init__(parent, Qt.Window | Qt.Dialog)
        self.setWindowTitle("设置")
        self.setMinimumSize(600, 540)
        self.ext_vars = {}       # {ext: checkbox, ...}
        self.cat_vars = {}       # {sub_cat_name: checkbox, ...}
        self.big_cat_vars = {}   # {big_cat_name: checkbox, ...}
        self.saved = False
        self.result_exts = current_exts
        self.result_full_hash = full_hash_enabled
        self.result_min_size = min_size_mb
        self.scan_all = scan_all  # True if exts is None

        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        # ---- Tab 1: Scan options ----
        scan_tab = QWidget()
        scan_layout = QVBoxLayout(scan_tab)
        scan_layout.addWidget(QLabel("扫描选项"))

        self.full_hash_cb = QCheckBox("全量哈希（精确模式，速度慢）")
        self.full_hash_cb.setChecked(full_hash_enabled)
        scan_layout.addWidget(self.full_hash_cb)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("最小文件大小 (MB):"))
        self.min_size_input = QLineEdit(str(min_size_mb))
        self.min_size_input.setFixedWidth(80)
        size_row.addWidget(self.min_size_input)
        size_row.addStretch()
        scan_layout.addLayout(size_row)
        scan_layout.addStretch()
        tabs.addTab(scan_tab, "扫描选项")

        # ---- Tab 2: File extensions ----
        ext_tab = QWidget()
        ext_layout = QVBoxLayout(ext_tab)

        # Scan all toggle
        self.scan_all_cb = QCheckBox("扫描所有文件（忽略后缀过滤）")
        self.scan_all_cb.setChecked(self.scan_all)
        self.scan_all_cb.toggled.connect(self._on_scan_all_toggled)
        ext_layout.addWidget(self.scan_all_cb)

        # Quick buttons for big categories
        qa = QHBoxLayout()
        for name in EXTENSION_CATEGORIES:
            btn = QPushButton(name)
            btn.clicked.connect(lambda checked, n=name: self._tgl_big_cat(n))
            qa.addWidget(btn)
        qa.addStretch()
        ext_layout.addLayout(qa)

        # Scrollable extension tree
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)

        for big_cat, sub_cats in EXTENSION_CATEGORIES.items():
            # Big category checkbox
            big_cb = QCheckBox(big_cat)
            big_cb.setChecked(False)  # Will be updated below
            big_cb.toggled.connect(lambda checked, bc=big_cat: self._tgl_big_cat_cb(bc, checked))
            self.big_cat_vars[big_cat] = big_cb
            scroll_layout.addWidget(big_cb)

            for sub_cat, exts in sub_cats.items():
                indent = QWidget()
                indent_layout = QHBoxLayout(indent)
                indent_layout.setContentsMargins(20, 0, 0, 0)

                sub_cb = QCheckBox(sub_cat)
                sub_cb.setChecked(all(e in current_exts for e in exts))
                sub_cb.toggled.connect(lambda checked, sc=sub_cat: self._tgl_sub_cat(sc, checked))
                self.cat_vars[sub_cat] = sub_cb
                indent_layout.addWidget(sub_cb)

                ext_row = QHBoxLayout()
                for ext in exts:
                    cb = QCheckBox(ext)
                    cb.setChecked(ext in (current_exts or set()))
                    self.ext_vars[ext] = cb
                    ext_row.addWidget(cb)
                ext_row.addStretch()
                indent_layout.addLayout(ext_row)
                indent_layout.addStretch()
                scroll_layout.addWidget(indent)

        # Update big category checkbox states
        for big_cat, sub_cats in EXTENSION_CATEGORIES.items():
            all_on = True
            for sub_cat, exts in sub_cats.items():
                if not all(e in (current_exts or set()) for e in exts):
                    all_on = False
                    break
            self.big_cat_vars[big_cat].setChecked(all_on)

        scroll.setWidget(scroll_content)
        ext_layout.addWidget(scroll)

        # Apply scan_all state
        self._on_scan_all_toggled(self.scan_all)
        tabs.addTab(ext_tab, "文件后缀")

        layout.addWidget(tabs)

        # Save / Cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.close)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("保存设置")
        save_btn.setStyleSheet("background-color: #3b82f6; color: white; font-weight: bold;")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _on_scan_all_toggled(self, checked):
        """When 'scan all' is checked, disable extension selection."""
        self.scan_all = checked
        for w in self.findChildren(QCheckBox):
            if w is not self.scan_all_cb and w is not self.full_hash_cb:
                w.setEnabled(not checked)
        if checked:
            # Clear all extension checkboxes
            for cb in self.ext_vars.values():
                cb.setChecked(False)
            for cb in self.cat_vars.values():
                cb.setChecked(False)
            for cb in self.big_cat_vars.values():
                cb.setChecked(False)

    def _tgl_big_cat(self, name):
        """Toggle all subcategories within a big category."""
        sub_cats = EXTENSION_CATEGORIES.get(name, {})
        # Determine if we should check or uncheck
        any_on = any(
            any(cb.isChecked() for e, cb in self.ext_vars.items() if e in exts)
            for exts in sub_cats.values()
        )
        for sub_cat, exts in sub_cats.items():
            self.cat_vars[sub_cat].setChecked(not any_on)
            for e in exts:
                if e in self.ext_vars:
                    self.ext_vars[e].setChecked(not any_on)
        self._sync_big_cats()

    def _tgl_big_cat_cb(self, big_cat, checked):
        """Toggle big category via its checkbox."""
        for sub_cat, exts in EXTENSION_CATEGORIES.get(big_cat, {}).items():
            self.cat_vars[sub_cat].setChecked(checked)
            for e in exts:
                if e in self.ext_vars:
                    self.ext_vars[e].setChecked(checked)

    def _tgl_sub_cat(self, sub_cat, checked):
        """Toggle all extensions in a sub-category."""
        for big_cat, sub_cats in EXTENSION_CATEGORIES.items():
            exts = sub_cats.get(sub_cat, [])
            if exts:
                for e in exts:
                    if e in self.ext_vars:
                        self.ext_vars[e].setChecked(checked)
                cat_cb = self.cat_vars.get(sub_cat)
                if cat_cb:
                    cat_cb.setChecked(checked)
                break
        self._sync_big_cats()

    def _sync_big_cats(self):
        """Update big category checkboxes based on sub-category states."""
        for big_cat, sub_cats in EXTENSION_CATEGORIES.items():
            all_on = True
            for sub_cat, exts in sub_cats.items():
                if not all(self.ext_vars[e].isChecked() for e in exts):
                    all_on = False
                    break
            self.big_cat_vars[big_cat].setChecked(all_on)

    def _save(self):
        if self.scan_all:
            self.result_exts = None  # None = scan all
        else:
            self.result_exts = {e for e, cb in self.ext_vars.items() if cb.isChecked()}
        self.result_full_hash = self.full_hash_cb.isChecked()
        self.result_min_size = int(self.min_size_input.text().strip() or "0")
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
        # Load saved scan options
        self.full_hash_enabled = False
        self.min_size_mb_val = MIN_FILE_SIZE_MB
        self._close_action = ''
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, encoding="utf-8") as f:
                    s = json.load(f)
                    self.full_hash_enabled = s.get("full_hash", False)
                    self.min_size_mb_val = s.get("min_size_mb", MIN_FILE_SIZE_MB)
                    self._close_action = s.get("close_action", '')
            except: pass

        self.db = Database()
        self.progress_queue = queue.Queue()
        self.dup_groups = []
        self.expanded_row = -1

        self.scan_worker = None

        self._build_ui()
        # System tray
        self._setup_tray()
        # Don't load old DB data — wait for fresh scan to complete
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
        ctrl_row.addStretch()
        main_layout.addLayout(ctrl_row)

        # ── Buttons ──
        btn_row = QHBoxLayout()

        self.status_lbl = QLabel("● 准备中...")
        self.status_lbl.setStyleSheet("color: #f59e0b; font-weight: bold;")
        btn_row.addWidget(self.status_lbl)

        self.refresh_btn = QPushButton("⟳ 全量刷新")
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        btn_row.addWidget(self.refresh_btn)

        btn_row.addStretch()

        for txt, slot in [("设置", self._open_settings)]:
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
        # Smooth progress animation
        self._prog_target = 0
        self._prog_current = 0
        self._prog_timer = QTimer()
        self._prog_timer.timeout.connect(self._animate_progress)
        self._prog_timer.start(30)  # ~33fps

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
        self.dup_table.setSortingEnabled(True)
        self.dup_table.horizontalHeader().setStretchLastSection(True)
        self.dup_table.horizontalHeader().setSortIndicatorShown(True)
        # Default sort: column 4 (wasted) descending
        self.dup_table.sortByColumn(4, Qt.DescendingOrder)
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

        # Tab 3: Folder comparison
        cmp_widget = QWidget()
        cmp_layout = QVBoxLayout(cmp_widget)
        cmp_layout.setContentsMargins(8, 8, 8, 8)

        # Folder selectors
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("文件夹 A:"))
        self.folder_a_input = QLineEdit()
        self.folder_a_input.setPlaceholderText("选择左侧文件夹...")
        folder_row.addWidget(self.folder_a_input)
        btn_a = QPushButton("浏览...")
        btn_a.clicked.connect(lambda: self._browse_folder(self.folder_a_input))
        folder_row.addWidget(btn_a)
        folder_row.addSpacing(20)
        folder_row.addWidget(QLabel("文件夹 B:"))
        self.folder_b_input = QLineEdit()
        self.folder_b_input.setPlaceholderText("选择右侧文件夹...")
        folder_row.addWidget(self.folder_b_input)
        btn_b = QPushButton("浏览...")
        btn_b.clicked.connect(lambda: self._browse_folder(self.folder_b_input))
        folder_row.addWidget(btn_b)
        cmp_layout.addLayout(folder_row)

        # Compare button
        cmp_btn_row = QHBoxLayout()
        self.cmp_btn = QPushButton("🔍 开始比对")
        self.cmp_btn.clicked.connect(self._start_folder_compare)
        cmp_btn_row.addWidget(self.cmp_btn)
        self.cmp_status = QLabel("")
        self.cmp_status.setStyleSheet("color: #6272a4;")
        cmp_btn_row.addWidget(self.cmp_status)
        cmp_btn_row.addStretch()
        # Delete action buttons
        self.cmp_del_left_btn = QPushButton("删除 ← 左边(A)的重复文件")
        self.cmp_del_left_btn.setStyleSheet("background-color: #ef4444; color: white;")
        self.cmp_del_left_btn.clicked.connect(lambda: self._cmp_delete_side("A"))
        self.cmp_del_left_btn.setEnabled(False)
        cmp_btn_row.addWidget(self.cmp_del_left_btn)
        self.cmp_del_right_btn = QPushButton("删除 → 右边(B)的重复文件")
        self.cmp_del_right_btn.setStyleSheet("background-color: #ef4444; color: white;")
        self.cmp_del_right_btn.clicked.connect(lambda: self._cmp_delete_side("B"))
        self.cmp_del_right_btn.setEnabled(False)
        cmp_btn_row.addWidget(self.cmp_del_right_btn)
        cmp_layout.addLayout(cmp_btn_row)

        # Results table
        self.cmp_table = QTableView()
        self.cmp_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cmp_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.cmp_table.horizontalHeader().setStretchLastSection(True)
        self.cmp_table.verticalHeader().setVisible(False)
        self.cmp_table.setShowGrid(False)
        self.cmp_table.setAlternatingRowColors(True)
        self.cmp_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cmp_table.customContextMenuRequested.connect(self._cmp_context_menu)
        self.cmp_table.doubleClicked.connect(self._cmp_open_file)
        self.cmp_model = CompareResultModel()
        self.cmp_table.setModel(self.cmp_model)
        self.cmp_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.cmp_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        cmp_layout.addWidget(self.cmp_table)

        self.tabs.addTab(cmp_widget, "文件夹比对")

        # Tab 4: Local dedup check
        local_widget = QWidget()
        local_layout = QVBoxLayout(local_widget)
        local_layout.setContentsMargins(8, 8, 8, 8)

        local_row = QHBoxLayout()
        local_row.addWidget(QLabel("文件夹:"))
        self.local_folder_input = QLineEdit()
        self.local_folder_input.setPlaceholderText("输入要查询的文件夹路径...")
        local_row.addWidget(self.local_folder_input)
        btn_local = QPushButton("浏览...")
        btn_local.clicked.connect(lambda: self._browse_folder(self.local_folder_input))
        local_row.addWidget(btn_local)
        local_layout.addLayout(local_row)

        local_btn_row = QHBoxLayout()
        self.local_check_btn = QPushButton("🔍 查询重复")
        self.local_check_btn.clicked.connect(self._start_local_check)
        local_btn_row.addWidget(self.local_check_btn)
        self.local_status = QLabel("")
        self.local_status.setStyleSheet("color: #6272a4;")
        local_btn_row.addWidget(self.local_status)
        local_btn_row.addStretch()
        self.local_del_btn = QPushButton("🗑 删除本文件夹（保留其他）")
        self.local_del_btn.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold;")
        self.local_del_btn.clicked.connect(self._local_delete_all)
        self.local_del_btn.setEnabled(False)
        local_btn_row.addWidget(self.local_del_btn)
        self.local_keep_btn = QPushButton("📌 保留本文件夹（删除其他）")
        self.local_keep_btn.setStyleSheet("background-color: #f59e0b; color: #0d1117; font-weight: bold;")
        self.local_keep_btn.clicked.connect(self._local_keep_all)
        self.local_keep_btn.setEnabled(False)
        local_btn_row.addWidget(self.local_keep_btn)
        local_layout.addLayout(local_btn_row)

        self.local_table = QTableView()
        self.local_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.local_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.local_table.horizontalHeader().setStretchLastSection(True)
        self.local_table.verticalHeader().setVisible(False)
        self.local_table.setShowGrid(False)
        self.local_table.setAlternatingRowColors(True)
        self.local_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.local_table.customContextMenuRequested.connect(self._local_context_menu)
        self.local_table.doubleClicked.connect(self._local_open_file)
        self.local_model = LocalCheckModel()
        self.local_table.setModel(self.local_model)
        self.local_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        local_layout.addWidget(self.local_table)

        self.tabs.addTab(local_widget, "本地查重")

        # Tab 5: Clean empty files
        empty_widget = QWidget()
        empty_layout = QVBoxLayout(empty_widget)
        empty_layout.setContentsMargins(8, 8, 8, 8)

        empty_btn_row = QHBoxLayout()
        self.empty_scan_btn = QPushButton("🔍 扫描空文件/空文件夹")
        self.empty_scan_btn.clicked.connect(self._start_empty_scan)
        empty_btn_row.addWidget(self.empty_scan_btn)
        self.empty_status = QLabel("")
        self.empty_status.setStyleSheet("color: #6272a4;")
        empty_btn_row.addWidget(self.empty_status)
        empty_btn_row.addStretch()
        self.empty_del_btn = QPushButton("🗑 删除选中的项目")
        self.empty_del_btn.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold;")
        self.empty_del_btn.clicked.connect(self._delete_empty_items)
        self.empty_del_btn.setEnabled(False)
        empty_btn_row.addWidget(self.empty_del_btn)
        self.empty_toggle_btn = QPushButton("全选")
        self.empty_toggle_btn.clicked.connect(self._empty_toggle_all)
        empty_btn_row.addWidget(self.empty_toggle_btn)
        empty_layout.addLayout(empty_btn_row)

        self.empty_table = QTableView()
        self.empty_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.empty_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.empty_table.horizontalHeader().setStretchLastSection(True)
        self.empty_table.verticalHeader().setVisible(False)
        self.empty_table.setShowGrid(False)
        self.empty_table.setAlternatingRowColors(True)
        self.empty_model = EmptyFilesModel()
        self.empty_table.setModel(self.empty_model)
        self.empty_table.clicked.connect(self._on_empty_table_clicked)
        self.empty_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        empty_layout.addWidget(self.empty_table)

        self.tabs.addTab(empty_widget, "清理空文件")

        # Tab 6: File tree
        tree_widget = QWidget()
        tree_layout = QVBoxLayout(tree_widget)
        tree_layout.setContentsMargins(4, 4, 4, 4)

        tree_btn_row = QHBoxLayout()
        self.tree_scan_btn = QPushButton("🔍 扫描盘符")
        self.tree_scan_btn.clicked.connect(self._start_tree_scan)
        tree_btn_row.addWidget(self.tree_scan_btn)
        self.tree_status = QLabel("")
        self.tree_status.setStyleSheet("color: #6272a4;")
        tree_btn_row.addWidget(self.tree_status)
        tree_btn_row.addStretch()
        tree_layout.addLayout(tree_btn_row)

        self.tree_view = QTreeView()
        self.tree_view.setAlternatingRowColors(True)
        self.tree_view.setAnimated(True)
        self.tree_view.setIndentation(20)
        self.tree_view.header().setStretchLastSection(False)
        self.tree_model = QStandardItemModel()
        self.tree_model.setHorizontalHeaderLabels(["名称", "大小", "文件数"])
        self.tree_view.setModel(self.tree_model)
        self.tree_view.setColumnWidth(0, 500)
        self.tree_view.setColumnWidth(1, 120)
        self.tree_view.setColumnWidth(2, 80)
        # Lazy loading: expand triggers population
        self.tree_view.expanded.connect(self._on_tree_expanded)
        self.tree_view.clicked.connect(self._on_tree_clicked)
        tree_layout.addWidget(self.tree_view)

        self.tabs.addTab(tree_widget, "文件树")

        # Tab 7: Disk analysis — directory tree with folder sizes
        disk_widget = QWidget()
        disk_layout = QVBoxLayout(disk_widget)
        disk_layout.setContentsMargins(4, 4, 4, 4)

        disk_btn_row = QHBoxLayout()
        self.disk_scan_btn = QPushButton("🔍 扫描盘符")
        self.disk_scan_btn.clicked.connect(self._start_disk_analysis)
        disk_btn_row.addWidget(self.disk_scan_btn)
        self.disk_status = QLabel("")
        self.disk_status.setStyleSheet("color: #6272a4;")
        disk_btn_row.addWidget(self.disk_status)
        disk_btn_row.addStretch()
        disk_layout.addLayout(disk_btn_row)

        self.disk_tree_view = QTreeView()
        self.disk_tree_view.setAlternatingRowColors(True)
        self.disk_tree_view.setAnimated(True)
        self.disk_tree_view.setIndentation(20)
        self.disk_tree_view.header().setStretchLastSection(False)
        self.disk_tree_model = QStandardItemModel()
        self.disk_tree_model.setHorizontalHeaderLabels(["名称", "大小", "文件数"])
        self.disk_tree_view.setModel(self.disk_tree_model)
        self.disk_tree_view.setColumnWidth(0, 500)
        self.disk_tree_view.setColumnWidth(1, 120)
        self.disk_tree_view.setColumnWidth(2, 80)
        self.disk_tree_view.expanded.connect(self._on_disk_tree_expanded)
        self.disk_tree_view.clicked.connect(self._on_disk_tree_clicked)
        disk_layout.addWidget(self.disk_tree_view)

        self.tabs.addTab(disk_widget, "盘符分析")

        # Tab 8: Extension statistics — expandable tree with per-drive breakdown
        ext_stats_widget = QWidget()
        ext_layout = QVBoxLayout(ext_stats_widget)
        ext_layout.setContentsMargins(4, 4, 4, 4)

        ext_btn_row = QHBoxLayout()
        self.ext_stats_btn = QPushButton("🔍 统计后缀")
        self.ext_stats_btn.clicked.connect(self._start_ext_stats)
        ext_btn_row.addWidget(self.ext_stats_btn)
        self.ext_stats_status = QLabel("")
        self.ext_stats_status.setStyleSheet("color: #6272a4;")
        ext_btn_row.addWidget(self.ext_stats_status)
        ext_btn_row.addStretch()
        ext_layout.addLayout(ext_btn_row)

        self.ext_stats_tree_view = QTreeView()
        self.ext_stats_tree_view.setAlternatingRowColors(True)
        self.ext_stats_tree_view.setAnimated(True)
        self.ext_stats_tree_view.setIndentation(20)
        self.ext_stats_tree_view.header().setStretchLastSection(False)
        self.ext_stats_tree_model = QStandardItemModel()
        self.ext_stats_tree_model.setHorizontalHeaderLabels(["后缀", "文件数", "总大小"])
        self.ext_stats_tree_view.setModel(self.ext_stats_tree_model)
        self.ext_stats_tree_view.setColumnWidth(0, 180)
        self.ext_stats_tree_view.setColumnWidth(1, 120)
        self.ext_stats_tree_view.setColumnWidth(2, 120)
        self.ext_stats_tree_view.setSortingEnabled(True)
        self.ext_stats_tree_view.sortByColumn(1, Qt.DescendingOrder)
        self.ext_stats_tree_view.expanded.connect(self._on_ext_stats_expanded)
        ext_layout.addWidget(self.ext_stats_tree_view)

        self.tabs.addTab(ext_stats_widget, "后缀统计")
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
        # Sync checkboxes with rubber band / shift-click selection
        self.dup_table.selectionModel().selectionChanged.connect(self._on_dup_selection_changed)
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

    def _load_data(self, on_done=None):
        # Save current sort state before reload
        hdr = self.dup_table.horizontalHeader()
        sort_col = hdr.sortIndicatorSection()
        sort_ord = hdr.sortIndicatorOrder()

        def _load():
            try:
                db = Database()
                stats = db.get_stats()
                total = stats["total_files"]
                dupc = stats["duplicate_groups"]
                wasted = format_size(stats["wasted_bytes"])
                self.sbar.showMessage(f"已索引 {total:,} 个文件  ·  {dupc:,} 组重复  ·  可释放 {wasted}")

                rows = db.search_files("", limit=200) if total > 0 else []
                data = [(fp, format_size(fs), f"{dc}个重复" if dc > 0 else "唯一")
                        for fp, fs, _, _, _, dc in rows]
                self.search_model.set_data(data)
                self.search_count.setText(f"显示前 {len(data)} / {total:,}")

                groups = db.get_duplicate_groups()
                dup_list = []
                for fhash, fsize, files in groups:
                    dup_list.append((fhash, fsize, sorted(files, key=lambda x: x[2])))
                dup_list.sort(key=lambda g: (len(g[2]) - 1) * g[1], reverse=True)
                self.dup_model.set_groups(dup_list)
                self.dup_groups = dup_list
                db.close()
                # Restore sort order AFTER data is loaded
                self.dup_table.sortByColumn(sort_col, sort_ord)
                if on_done:
                    on_done()
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
        # Full scan on startup
        QTimer.singleShot(600, lambda: self._run_scan(True))

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
        # Clear tables to prevent accidental operations on stale data
        self.dup_model.set_groups([])
        self.detail_widget.setVisible(False)
        self.search_model.set_data([])
        self.status_lbl.setText("● 扫描中...")
        self.status_lbl.setStyleSheet("color: #f59e0b; font-weight: bold;")
        self.refresh_btn.setEnabled(False)
        self._prog_target = 0
        self._prog_current = 0
        self.prog_bar.setValue(0)

        exts = self.active_exts if self.active_exts else None
        min_mb = self.min_size_mb_val

        self.scan_worker = ScanWorker(drives, force, exts,
                                      not self.full_hash_enabled, min_mb)
        self.scan_worker.progress.connect(self._on_scan_progress)
        self.scan_worker.finished.connect(self._on_scan_done)
        self.scan_worker.error.connect(self._on_scan_error)
        self.scan_worker.start()

    @Slot(str, str)
    def _on_scan_progress(self, stage, msg):
        if stage == "timing":
            self._scan_timing = msg  # saved for _on_scan_done
        else:
            pct = self._calc_pct(stage, msg)
            self._prog_target = int(pct * 100)
            self.prog_text.setText(f"{pct:.2f}%  {msg}")

    @Slot(list, object, object)
    def _on_scan_done(self, dup_list, total, wasted):
        self.scan_running = False
        self._prog_target = 10000
        self._prog_current = 10000
        self.prog_bar.setValue(10000)
        # Use DB stats for consistent numbers with status bar
        db = Database()
        stats = db.get_stats()
        db.close()
        db_wasted = format_size(stats["wasted_bytes"])
        db_groups = stats["duplicate_groups"]
        timing = getattr(self, '_scan_timing', '')
        timing_text = f"  |  {timing}" if timing else ""
        self.prog_text.setText(f"100.00%  扫描完成  ·  {stats['total_files']:,} 文件  ·  {db_groups:,} 组重复  ·  可释放 {db_wasted}{timing_text}")
        self.status_lbl.setText("✓ 已是最新")
        self.status_lbl.setStyleSheet("color: #22c55e; font-weight: bold;")
        self.refresh_btn.setEnabled(True)
        self.dup_groups = dup_list
        self.dup_model.set_groups(dup_list)
        self._update_drive_capacity()
        self._load_data()

    @Slot(str)
    def _on_scan_error(self, err):
        self.scan_running = False
        self.prog_text.setText(f"错误: {err}")
        self.status_lbl.setText("✗ 扫描出错")
        self.status_lbl.setStyleSheet("color: #ff5555; font-weight: bold;")
        self.refresh_btn.setEnabled(True)

    def _animate_progress(self):
        """Smoothly animate progress bar toward target."""
        if self._prog_current < self._prog_target:
            # Move 1-5% of the gap per frame, faster for bigger gaps
            gap = self._prog_target - self._prog_current
            step = max(1, gap // 20)  # ~0.5s to cover any gap at 30fps
            self._prog_current = min(self._prog_current + step, self._prog_target)
            self.prog_bar.setValue(self._prog_current)
        elif self._prog_current > self._prog_target:
            self._prog_current = self._prog_target
            self.prog_bar.setValue(self._prog_current)

    def _calc_pct(self, stage, msg):
        fast = not self.full_hash_enabled
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

        self.status_lbl.setText("● 清理中...")
        self.status_lbl.setStyleSheet("color: #f59e0b; font-weight: bold;")

        def _run():
            deleted = 0
            errors = 0
            for ghash, fsize, files in groups:
                files_sorted = sorted(files, key=lambda x: x[2])
                for fp, fs, mt in files_sorted[1:]:
                    try:
                        if os.path.exists(fp): send2trash(fp)
                        db = Database()
                        db.conn.execute("DELETE FROM file_index WHERE file_path=?", (fp,))
                        db.conn.commit(); db.close()
                        deleted += 1
                    except Exception:
                        errors += 1

            saved = format_size(total_wasted) if errors == 0 else "部分"
            self.sbar.showMessage(f"批量清理完成: 删除 {deleted} 个文件，释放约 {saved}")
            self.status_lbl.setText("✓ 已是最新")
            self.status_lbl.setStyleSheet("color: #22c55e; font-weight: bold;")
            self._load_data()
            self.detail_widget.setVisible(False)

        threading.Thread(target=_run, daemon=True).start()

    def _on_dup_selection_changed(self, selected, deselected):
        """Toggle checkboxes for rows added/removed during drag or shift-click."""
        is_drag = bool(QApplication.mouseButtons() & Qt.LeftButton)
        is_shift = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
        if not (is_drag or is_shift):
            return
        # For shift-click, process all currently-selected rows
        sel_rows = {i.row() for i in self.dup_table.selectionModel().selectedRows()}
        if len(sel_rows) <= 1:
            return  # Single click handled by _on_dup_table_clicked
        rows_added = set()
        for rng in selected:
            for row in range(rng.top(), rng.bottom() + 1):
                rows_added.add(row)
        if not rows_added:
            return
        make_checked = min(rows_added) not in self.dup_model._checked
        if make_checked:
            self.dup_model._checked.update(sel_rows)
        else:
            self.dup_model._checked.difference_update(sel_rows)
        if sel_rows:
            top = self.dup_model.index(min(sel_rows), 0)
            bot = self.dup_model.index(max(sel_rows), 0)
            self.dup_model.dataChanged.emit(top, bot, [Qt.CheckStateRole])
        self._update_batch_ui()

    def _on_dup_current_changed(self, current, previous):
        """Keyboard navigation: update detail panel when selection moves."""
        if not current.isValid(): return
        row = current.row()
        if 0 <= row < len(self.dup_model._groups):
            ghash, fsize, files = self.dup_model._groups[row]
            self.detail_model.set_files(files)
            self.detail_widget.setVisible(True)
            self._update_toggle_btn()
            self._current_model_row = row

    def _on_dup_table_clicked(self, index):
        """Clicking any column on a row toggles its checkbox."""
        r = index.row()
        if r in self.dup_model._checked:
            self.dup_model._checked.discard(r)
        else:
            self.dup_model._checked.add(r)
        # Refresh the checkbox column
        cb_idx = self.dup_model.index(r, 0)
        self.dup_model.dataChanged.emit(cb_idx, cb_idx, [Qt.CheckStateRole])
        self._update_batch_ui()

        # Also update the detail panel
        if 0 <= r < len(self.dup_model._groups):
            ghash, fsize, files = self.dup_model._groups[r]
            self.detail_model.set_files(files)
            self.detail_widget.setVisible(True)
            self._update_toggle_btn()
            self._current_model_row = r

    def _reveal_dup_file(self, index):
        row = index.row()
        if 0 <= row < len(self.dup_model._groups):
            _reveal_in_explorer(self.dup_model._groups[row][2][0][0])

    def _on_detail_checkbox_clicked(self, index):
        """Clicking any column on a detail row toggles its checkbox."""
        fp, fs, mt, checked = self.detail_model._files[index.row()]
        new_state = not checked
        self.detail_model._files[index.row()] = (fp, fs, mt, new_state)
        self.detail_model.dataChanged.emit(index, index, [Qt.CheckStateRole])
        self._update_toggle_btn()

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
                if os.path.exists(fp): send2trash(fp)
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
            if os.path.exists(fp): send2trash(fp)
            db = Database()
            db.conn.execute("DELETE FROM file_index WHERE file_path=?", (fp,))
            db.conn.commit(); db.close()
            self.sbar.showMessage(f"已删除: {os.path.basename(fp)}")
            self._update_drive_capacity()

            # Immediately update detail model — remove the deleted file
            new_files = [(p, s, m, c) for p, s, m, c in self.detail_model._files if p != fp]
            if len(new_files) <= 1:
                # No longer a duplicate group — remove from model
                self.detail_widget.setVisible(False)
                if hasattr(self, '_current_model_row'):
                    r = self._current_model_row
                    if r < len(self.dup_model._groups):
                        self.dup_model._groups.pop(r)
                        self.dup_model.set_groups(self.dup_model._groups)
            else:
                self.detail_model._files = new_files
                self.detail_model.beginResetModel()
                self.detail_model.endResetModel()
                # Update the group's files in the model
                if hasattr(self, '_current_model_row'):
                    r = self._current_model_row
                    if r < len(self.dup_model._groups):
                        ghash, fsize, old_files = self.dup_model._groups[r]
                        remaining = [(p, s, m) for p, s, m in old_files if p != fp]
                        if remaining:
                            self.dup_model._groups[r] = (ghash, fsize, remaining)
                            self.dup_model.set_groups(self.dup_model._groups)

            self._load_data()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"删除失败: {e}")

    # ── Settings ──────────────────────────────────────────────────────────

    def _open_settings(self):
        scan_all = self.active_exts is None
        dlg = SettingsDialog(self.active_exts or set(), self.full_hash_enabled,
                             self.min_size_mb_val, scan_all, self)
        dlg.show()
        while dlg.isVisible():
            QApplication.processEvents()
        if dlg.saved:
            self.active_exts = dlg.result_exts  # None = scan all
            self.full_hash_enabled = dlg.result_full_hash
            self.min_size_mb_val = dlg.result_min_size
            # Persist — load old to preserve close_action etc.
            s = {}
            if os.path.exists(SETTINGS_FILE):
                try: s = json.load(open(SETTINGS_FILE, encoding="utf-8"))
                except: pass
            s["full_hash"] = dlg.result_full_hash
            s["min_size_mb"] = dlg.result_min_size
            if dlg.result_exts is not None:
                s["extensions"] = sorted(dlg.result_exts)
            else:
                s["scan_all"] = True
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(s, f, ensure_ascii=False, indent=2)
            count = "全部" if dlg.result_exts is None else len(dlg.result_exts)
            self.sbar.showMessage(
                f"设置已保存 ({count} 个后缀, 最小 {dlg.result_min_size} MB)")
            # Stop current scan (if any) and restart with new settings
            self.scan_running = False
            QTimer.singleShot(300, lambda: self._run_scan(True))

    # ── Export ────────────────────────────────────────────────────────────

    # ── Folder Compare ───────────────────────────────────────────────────

    def _browse_folder(self, line_edit):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            line_edit.setText(folder)

    def _start_folder_compare(self):
        fa = self.folder_a_input.text().strip()
        fb = self.folder_b_input.text().strip()
        if not fa or not fb:
            QMessageBox.warning(self, "提示", "请先选择两个文件夹")
            return

        self.cmp_btn.setEnabled(False)
        self.cmp_status.setText("● 比对中...")
        self.cmp_status.setStyleSheet("color: #f59e0b;")

        exts = self.active_exts if self.active_exts else None

        def _run():
            try:
                import scanner
                scanner.MIN_FILE_SIZE = 1  # check all files

                hashes_a = {}
                for fp, fsize, _ in walk_files([fa], extensions=exts):
                    qh = quick_hash(fp)
                    if qh:
                        if qh not in hashes_a:
                            hashes_a[qh] = []
                        hashes_a[qh].append((fp, fsize))

                hashes_b = {}
                for fp, fsize, _ in walk_files([fb], extensions=exts):
                    qh = quick_hash(fp)
                    if qh:
                        if qh not in hashes_b:
                            hashes_b[qh] = []
                        hashes_b[qh].append((fp, fsize))

                # Find matching hashes
                common = set(hashes_a.keys()) & set(hashes_b.keys())
                pairs = []
                for qh in sorted(common):
                    for pa, fsize in hashes_a[qh]:
                        for pb, _ in hashes_b[qh]:
                            pairs.append((qh, fsize, pa, pb))

                pairs.sort(key=lambda x: x[1], reverse=True)
                self.cmp_model.set_pairs(pairs)

                wasted = sum(p[1] for p in pairs)
                self.cmp_status.setText(
                    f"✓ 完成: {len(pairs)} 对重复  |  每对可释放 {format_size(wasted) if wasted > 0 else '0 B'}")
                self.cmp_status.setStyleSheet("color: #22c55e;")
                self.cmp_del_left_btn.setEnabled(len(pairs) > 0)
                self.cmp_del_right_btn.setEnabled(len(pairs) > 0)
            except Exception as e:
                self.cmp_status.setText(f"✗ 错误: {e}")
                self.cmp_status.setStyleSheet("color: #ff5555;")
            finally:
                self.cmp_btn.setEnabled(True)

        threading.Thread(target=_run, daemon=True).start()

    def _cmp_delete_side(self, side):
        pairs = self.cmp_model._pairs
        if not pairs: return
        idx = 2 if side == "A" else 3  # p[2]=path_a, p[3]=path_b in tuple (hash, size, pa, pb)
        count = len(pairs)
        wasted = sum(p[1] for p in pairs)
        label = "左边(A)" if side == "A" else "右边(B)"

        reply = QMessageBox.question(self, "确认删除",
            f"将删除 {label} 的 {count} 个重复文件\n"
            f"预计释放 {format_size(wasted)}\n\n确定？")
        if reply != QMessageBox.Yes: return

        deleted = 0
        for p in pairs:
            fp = p[idx]
            try:
                if os.path.exists(fp): send2trash(fp)
                deleted += 1
            except Exception:
                pass

        self.cmp_model.set_pairs([])
        self.cmp_status.setText(f"✓ 已删除 {label}: {deleted} 个文件  |  释放 {format_size(wasted)}")
        self.cmp_del_left_btn.setEnabled(False)
        self.cmp_del_right_btn.setEnabled(False)

    def _cmp_context_menu(self, pos):
        index = self.cmp_table.indexAt(pos)
        if not index.isValid(): return
        pa, pb = self.cmp_model.data(index, Qt.UserRole) or ("", "")
        menu = QMenu(self)
        menu.addAction("📂 打开 A 位置", lambda: _reveal_in_explorer(pa))
        menu.addAction("📂 打开 B 位置", lambda: _reveal_in_explorer(pb))
        menu.addSeparator()
        menu.addAction("🗑 删除 A", lambda: self._delete_one_file(pa))
        menu.addAction("🗑 删除 B", lambda: self._delete_one_file(pb))
        menu.exec(QCursor.pos())

    def _cmp_open_file(self, index):
        pa, pb = self.cmp_model.data(index, Qt.UserRole) or ("", "")
        if pa: _reveal_in_explorer(pa)

    # ── Local Dedup Check ────────────────────────────────────────────────

    def _start_local_check(self):
        folder = self.local_folder_input.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, "提示", "请输入有效的文件夹路径")
            return

        self.local_check_btn.setEnabled(False)
        self.local_status.setText("● 查询中...")
        self.local_status.setStyleSheet("color: #f59e0b;")
        self.local_del_btn.setEnabled(False)

        exts = self.active_exts if self.active_exts else None

        def _run():
            try:
                import scanner
                scanner.MIN_FILE_SIZE = 1  # check all files

                # Get quick hashes for files in this folder
                local_files = {}
                for fp, fsize, _ in walk_files([folder], extensions=exts):
                    qh = quick_hash(fp)
                    if qh:
                        if qh not in local_files:
                            local_files[qh] = []
                        local_files[qh].append((fp, fsize))

                # For each hash, search DB for matching files elsewhere
                rows = []
                db = Database()
                for qh, files in local_files.items():
                    db_files = db.conn.execute(
                        "SELECT file_path, file_size FROM file_index "
                        "WHERE (quick_hash = ? OR full_hash = ?) AND status = 'full_hashed'",
                        (qh, qh)
                    ).fetchall()
                    # Filter: exclude files already in the scanned folder
                    dup_elsewhere = [(fp, fs) for fp, fs in db_files
                                     if not fp.startswith(folder)]
                    if dup_elsewhere:
                        for local_fp, local_fs in files:
                            rows.append((local_fp, max(local_fs, dup_elsewhere[0][1]),
                                         [f"{fp}  ({format_size(fs)})" for fp, fs in dup_elsewhere]))
                db.close()

                rows.sort(key=lambda r: r[1], reverse=True)

                self.local_model.set_rows(rows)
                wasted = sum(r[1] for r in rows)
                self.local_status.setText(
                    f"✓ {len(rows)} 个文件在其他位置有重复  |  可释放 {format_size(wasted) if rows else '0 B'}")
                self.local_status.setStyleSheet("color: #22c55e;")
                self.local_del_btn.setEnabled(len(rows) > 0)
                self.local_keep_btn.setEnabled(len(rows) > 0)
            except Exception as e:
                self.local_status.setText(f"✗ 错误: {e}")
                self.local_status.setStyleSheet("color: #ff5555;")
            finally:
                self.local_check_btn.setEnabled(True)

        threading.Thread(target=_run, daemon=True).start()

    def _local_delete_all(self):
        paths = self.local_model.get_all_local_paths()
        if not paths: return
        wasted = sum(r[1] for r in self.local_model._rows)
        reply = QMessageBox.question(self, "确认删除",
            f"将删除本文件夹内的 {len(paths)} 个文件\n"
            f"保留其他位置的副本\n预计释放 {format_size(wasted)}\n\n确定？")
        if reply != QMessageBox.Yes: return

        deleted = 0
        for fp in paths:
            try:
                if os.path.exists(fp): send2trash(fp)
                db = Database()
                db.conn.execute("DELETE FROM file_index WHERE file_path=?", (fp,))
                db.conn.commit(); db.close()
                deleted += 1
            except Exception:
                pass
        self.local_model.set_rows([])
        self.local_status.setText(f"✓ 已删除 {deleted} 个文件  |  释放 {format_size(wasted)}")
        self.local_del_btn.setEnabled(False)
        self.local_keep_btn.setEnabled(False)

    def _local_keep_all(self):
        """Keep local files, delete all duplicates elsewhere."""
        # Collect all duplicate paths from the model rows (column 2)
        all_dup_paths = []
        wasted = 0
        for local_path, fsize, dup_paths in self.local_model._rows:
            wasted += fsize
            for dp in dup_paths:
                # Extract path from "path  (size)" format
                fp = dp.rsplit("  (", 1)[0] if "  (" in dp else dp
                all_dup_paths.append(fp)
        if not all_dup_paths: return

        reply = QMessageBox.question(self, "确认删除",
            f"保留本文件夹的文件，删除其他位置的 {len(all_dup_paths)} 个副本\n"
            f"预计释放 {format_size(wasted)}\n\n确定？")
        if reply != QMessageBox.Yes: return

        deleted = 0
        for fp in all_dup_paths:
            try:
                if os.path.exists(fp): send2trash(fp)
                db = Database()
                db.conn.execute("DELETE FROM file_index WHERE file_path=?", (fp,))
                db.conn.commit(); db.close()
                deleted += 1
            except Exception:
                pass
        self.local_model.set_rows([])
        self.local_status.setText(f"✓ 保留本地，已删除其他位置 {deleted} 个文件")
        self.local_del_btn.setEnabled(False)
        self.local_keep_btn.setEnabled(False)

    def _local_context_menu(self, pos):
        index = self.local_table.indexAt(pos)
        if not index.isValid(): return
        fp = self.local_model.data(index, Qt.UserRole)
        if not fp: return
        menu = QMenu(self)
        menu.addAction("📂 打开文件位置", lambda: _reveal_in_explorer(fp))
        menu.addAction("📋 复制路径", lambda: QApplication.clipboard().setText(fp))
        menu.addSeparator()
        menu.addAction("🗑 删除此文件", lambda: self._delete_one_file(fp))
        menu.exec(QCursor.pos())

    def _local_open_file(self, index):
        fp = self.local_model.data(index, Qt.UserRole)
        if fp: _reveal_in_explorer(fp)

    # ── Empty Files Cleanup ──────────────────────────────────────────────

    def _start_empty_scan(self):
        drives = [f"{d}:\\" for d, cb in self.drive_checks.items() if cb.isChecked()]
        if not drives:
            QMessageBox.warning(self, "提示", "请至少勾选一个盘符")
            return

        self.empty_scan_btn.setEnabled(False)
        self.empty_status.setText("● 扫描中...")
        self.empty_status.setStyleSheet("color: #f59e0b;")
        self.empty_del_btn.setEnabled(False)

        def _run():
            items = []
            for drive in drives:
                try:
                    for root, dirs, files in os.walk(drive):
                        # Skip system dirs
                        dirs[:] = [d for d in dirs if d not in {
                            "$RECYCLE.BIN", "System Volume Information",
                            "Windows", "Program Files", "Program Files (x86)",
                            "ProgramData", "Recovery", ".git", "__pycache__",
                            "node_modules"} and not d.startswith(".")]

                        for fname in files:
                            fp = os.path.join(root, fname)
                            try:
                                if os.path.getsize(fp) == 0 and os.path.isfile(fp):
                                    items.append(("空文件", fp, 0))
                                    # Limit to avoid memory issues
                                    if len(items) >= 50000:
                                        break
                            except OSError:
                                pass
                        if len(items) >= 50000:
                            break

                        # Empty folder (no files and no subdirs)
                        if not files and not dirs:
                            items.append(("空文件夹", root, 0))
                except OSError:
                    pass

            # Sort: folders first, then by path
            items.sort(key=lambda x: (0 if "夹" in x[0] else 1, x[1]))
            self.empty_model.set_items(items)
            self.empty_status.setText(f"✓ 找到 {len(items)} 个空文件/空文件夹")
            self.empty_status.setStyleSheet("color: #22c55e;")
            self.empty_del_btn.setEnabled(len(items) > 0)
            self.empty_scan_btn.setEnabled(True)

        threading.Thread(target=_run, daemon=True).start()

    def _on_empty_table_clicked(self, index):
        if index.column() == 0:
            r = index.row()
            if r in self.empty_model._checked:
                self.empty_model._checked.discard(r)
            else:
                self.empty_model._checked.add(r)
            self.empty_model.dataChanged.emit(index, index, [Qt.CheckStateRole])
            self._update_empty_toggle_btn()

    def _empty_toggle_all(self):
        if self.empty_model.get_checked_count() == len(self.empty_model._items):
            self.empty_model.uncheck_all()
        else:
            self.empty_model.check_all()
        self._update_empty_toggle_btn()

    def _update_empty_toggle_btn(self):
        n = self.empty_model.get_checked_count()
        all_checked = n == len(self.empty_model._items) and n > 0
        self.empty_toggle_btn.setText("取消全选" if all_checked else "全选")

    def _delete_empty_items(self):
        checked = self.empty_model.get_checked()
        if not checked:
            QMessageBox.warning(self, "提示", "请勾选要删除的项目")
            return

        empty_files = sum(1 for t, _, _ in checked if "文件" in t)
        empty_dirs = len(checked) - empty_files
        reply = QMessageBox.question(self, "确认删除",
            f"将删除 {empty_files} 个空文件 + {empty_dirs} 个空文件夹\n共 {len(checked)} 项\n\n确定？")
        if reply != QMessageBox.Yes: return

        deleted = 0
        errors = 0
        for typ, path, _ in checked:
            try:
                send2trash(path)
                deleted += 1
            except Exception:
                errors += 1

        self.empty_status.setText(f"✓ 已删除 {deleted} 项" + (f"  ({errors} 失败)" if errors else ""))
        self.empty_del_btn.setEnabled(False)
        # Don't clear list so user can see what was done; they can re-scan

    # ── Clean ─────────────────────────────────────────────────────────────

    # ── System Tray ────────────────────────────────────────────────────────

    def _setup_tray(self):
        """Create system tray icon with context menu."""
        self.tray_icon = QSystemTrayIcon(self)
        icon_path = _resource_path("app_icon.ico")
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        self.tray_icon.setToolTip("CleanDup")

        menu = QMenu()
        menu.addAction("📂 显示窗口", self._tray_show)
        menu.addAction("⟳ 全量刷新", lambda: self._run_scan(True))
        menu.addSeparator()
        menu.addAction("❌ 退出", self._tray_exit)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        """Double-click tray icon to restore window."""
        if reason == QSystemTrayIcon.DoubleClick:
            self._tray_show()

    def _tray_show(self):
        """Restore window from tray."""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _tray_exit(self):
        """Fully exit the application."""
        self.tray_icon.hide()
        QApplication.quit()

    def closeEvent(self, event: QCloseEvent):
        """Show dialog or use saved preference for close behavior."""
        saved = getattr(self, '_close_action', '')
        if saved == 'tray':
            self.hide(); event.ignore(); return
        if saved == 'quit':
            self.tray_icon.hide(); event.accept(); return

        msg = QMessageBox(self)
        msg.setWindowTitle("CleanDup")
        msg.setText("请选择操作")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        msg.button(QMessageBox.Yes).setText("最小化到托盘")
        msg.button(QMessageBox.No).setText("直接关闭")
        msg.button(QMessageBox.Cancel).setText("取消")
        cb = QCheckBox("记住我的选择，下次不再询问")
        msg.setCheckBox(cb)
        reply = msg.exec()

        if reply == QMessageBox.Yes:
            self.hide(); event.ignore()
            action = 'tray'
        elif reply == QMessageBox.No:
            self.tray_icon.hide(); event.accept()
            action = 'quit'
        else:
            event.ignore(); return

        if cb.isChecked():
            self._close_action = action
            s = {}
            if os.path.exists(SETTINGS_FILE):
                try: s = json.load(open(SETTINGS_FILE, encoding="utf-8"))
                except: pass
            s["close_action"] = action
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(s, f, ensure_ascii=False, indent=2)

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
                    self.refresh_btn.setEnabled(True)
                    self.dup_groups = dup_list
                    self.dup_model.set_groups(dup_list)
                    self._load_data()
                elif msg[0] == "error":
                    self.prog_text.setText(f"错误: {msg[1]}")
                    self.refresh_btn.setEnabled(True)
        except queue.Empty:
            pass


    # ── Disk Analysis ──────────────────────────────────────────────────────

    # ── Disk Analysis (tree view) ──────────────────────────────────────────

    def _start_disk_analysis(self):
        """Populate root level with selected drives, showing total capacity."""
        self.disk_tree_model.removeRows(0, self.disk_tree_model.rowCount())
        self.disk_scan_btn.setEnabled(False)
        self.disk_status.setText("● 扫描中...")
        self.disk_status.setStyleSheet("color: #f59e0b;")

        drives = {d for d, cb in self.drive_checks.items() if cb.isChecked()}
        if not drives:
            drives = {d for d in self.drive_checks}

        import shutil
        for d in sorted(drives):
            path = f"{d}:\\"
            try:
                usage = shutil.disk_usage(path)
                total_str = format_size(usage.total)
            except Exception:
                total_str = "..."
            root = QStandardItem(f"{d}:  ({total_str})")
            root.setData(path, Qt.UserRole)
            root.setData(True, Qt.UserRole + 1)  # needs loading
            sz = QStandardItem(total_str)
            sz.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            cnt = QStandardItem("")
            self.disk_tree_model.appendRow([root, sz, cnt])
            root.appendRow([QStandardItem("..."), QStandardItem(""), QStandardItem("")])

        self.disk_status.setText("✓ 点击展开查看子目录")
        self.disk_status.setStyleSheet("color: #22c55e;")
        self.disk_scan_btn.setEnabled(True)

    def _on_disk_tree_expanded(self, index):
        """Lazy load children with recursive sizes."""
        item = self.disk_tree_model.itemFromIndex(index)
        if not item or not item.data(Qt.UserRole + 1):
            return
        item.setData(False, Qt.UserRole + 1)
        item.removeRows(0, item.rowCount())
        path = item.data(Qt.UserRole)
        if not path: return

        def _run():
            children = []
            skip_dirs = {"$RECYCLE.BIN", "System Volume Information", "Windows",
                         "Program Files", "Program Files (x86)", "ProgramData", "Recovery"}
            try:
                for entry in os.scandir(path):
                    try:
                        if entry.is_dir() and not entry.name.startswith('.') and entry.name not in skip_dirs:
                            full = entry.path
                            size = _get_dir_size_recursive(full)
                            size_str = format_size(size) if size > 0 else "0 B"
                            children.append((entry.name, full, size, size_str))
                    except OSError:
                        pass
            except OSError:
                pass

            children.sort(key=lambda x: x[2], reverse=True)
            for name, full, size, size_str in children:
                child = QStandardItem(name)
                child.setData(full, Qt.UserRole)
                child.setData(True, Qt.UserRole + 1)
                sz = QStandardItem(size_str)
                sz.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                cnt = QStandardItem("")
                child.appendRow([QStandardItem("..."), QStandardItem(""), QStandardItem("")])
                item.appendRow([child, sz, cnt])

        threading.Thread(target=_run, daemon=True).start()

    def _on_disk_tree_clicked(self, index):
        """Click on a placeholder node triggers lazy load."""
        item = self.disk_tree_model.itemFromIndex(index)
        if item and item.data(Qt.UserRole + 1):
            self.disk_tree_view.expand(index)

    # ── Extension Stats (tree view with per-drive breakdown) ───────────────

    def _start_ext_stats(self):
        """Count files by extension across drives. Root items are extensions,
        children show per-drive breakdown (lazy loaded on expand)."""
        drives = [f"{d}:\\" for d, cb in self.drive_checks.items() if cb.isChecked()]
        if not drives:
            drives = [f"{d}:\\" for d in self.drive_checks]

        self.ext_stats_btn.setEnabled(False)
        self.ext_stats_status.setText("● 统计中...")
        self.ext_stats_status.setStyleSheet("color: #f59e0b;")

        exts_filter = self.active_exts if self.active_exts and self.active_exts else None

        def _run():
            # {ext: {drive_letter: [count, total_size]}}
            ext_drive_map = {}
            total = 0

            skip_dirs = {"$RECYCLE.BIN", "System Volume Information",
                         "Windows", "Program Files", "Program Files (x86)",
                         "ProgramData", "Recovery", ".git", "__pycache__"}

            for drive_path in drives:
                drive_letter = drive_path[0]  # e.g. "D"
                try:
                    for root, dirs, files in os.walk(drive_path):
                        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in skip_dirs]
                        for fname in files:
                            ext = os.path.splitext(fname)[1].lower()
                            if exts_filter and ext not in exts_filter:
                                continue
                            if ext not in ext_drive_map:
                                ext_drive_map[ext] = {}
                            if drive_letter not in ext_drive_map[ext]:
                                ext_drive_map[ext][drive_letter] = [0, 0]
                            ext_drive_map[ext][drive_letter][0] += 1
                            total += 1
                except OSError:
                    pass

            self.ext_stats_tree_model.removeRows(0, self.ext_stats_tree_model.rowCount())

            # Sort extensions by total count desc
            ext_totals = []
            for ext, drive_data in ext_drive_map.items():
                total_count = sum(v[0] for v in drive_data.values())
                ext_totals.append((ext, total_count, drive_data))
            ext_totals.sort(key=lambda x: x[1], reverse=True)

            for ext, total_count, drive_data in ext_totals:
                ext_item = QStandardItem(ext or "(无后缀)")
                ext_item.setData(drive_data, Qt.UserRole)  # store per-drive data
                ext_item.setData(True, Qt.UserRole + 1)  # needs loading
                count_item = QStandardItem(f"{total_count:,}")
                count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                sz_item = QStandardItem("")
                self.ext_stats_tree_model.appendRow([ext_item, count_item, sz_item])
                # Placeholder for lazy loading
                ext_item.appendRow([QStandardItem("..."), QStandardItem(""), QStandardItem("")])

            self.ext_stats_status.setText(f"✓ {total:,} 个文件  |  {len(ext_totals)} 种后缀")
            self.ext_stats_status.setStyleSheet("color: #22c55e;")
            self.ext_stats_btn.setEnabled(True)

        threading.Thread(target=_run, daemon=True).start()

    def _on_ext_stats_expanded(self, index):
        """Lazy load per-drive breakdown for an extension."""
        item = self.ext_stats_tree_model.itemFromIndex(index)
        if not item or not item.data(Qt.UserRole + 1):
            return
        item.setData(False, Qt.UserRole + 1)
        item.removeRows(0, item.rowCount())

        drive_data = item.data(Qt.UserRole)  # {drive_letter: [count, total_size]}
        if not isinstance(drive_data, dict):
            return

        # Sort drives by count desc
        sorted_drives = sorted(drive_data.items(), key=lambda x: x[1][0], reverse=True)
        for drive_letter, (cnt, sz) in sorted_drives:
            drive_item = QStandardItem(f"{drive_letter}: 盘")
            count_item = QStandardItem(f"{cnt:,}")
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            sz_item = QStandardItem(format_size(sz) if sz > 0 else "")
            sz_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.appendRow([drive_item, count_item, sz_item])

    def _start_tree_scan(self):
        """Populate root level with selected drives (immediate sizes only)."""
        self.tree_model.removeRows(0, self.tree_model.rowCount())
        self.tree_scan_btn.setEnabled(False)
        self.tree_status.setText("● 扫描中...")
        self.tree_status.setStyleSheet("color: #f59e0b;")

        drives = {d for d, cb in self.drive_checks.items() if cb.isChecked()}
        if not drives:
            drives = {d for d, cb in self.drive_checks.items()}

        import shutil
        for d in sorted(drives):
            path = f"{d}:\\"
            try:
                usage = shutil.disk_usage(path)
                total_str = format_size(usage.total)
            except Exception:
                total_str = "..."
            root = QStandardItem(f"{d}:  ({total_str})")
            root.setData(path, Qt.UserRole)
            root.setData(True, Qt.UserRole + 1)  # needs loading
            sz = QStandardItem(total_str)
            sz.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            cnt = QStandardItem("")
            self.tree_model.appendRow([root, sz, cnt])
            root.appendRow([QStandardItem("..."), QStandardItem(""), QStandardItem("")])

        self.tree_status.setText("✓ 点击展开查看子目录")
        self.tree_status.setStyleSheet("color: #22c55e;")
        self.tree_scan_btn.setEnabled(True)

    def _on_tree_expanded(self, index):
        """Lazy load children with recursive sizes when a node is expanded."""
        item = self.tree_model.itemFromIndex(index)
        if not item or not item.data(Qt.UserRole + 1):
            return
        item.setData(False, Qt.UserRole + 1)
        item.removeRows(0, item.rowCount())
        path = item.data(Qt.UserRole)
        if not path: return

        def _run():
            children = []
            try:
                for entry in os.scandir(path):
                    try:
                        if entry.is_dir() and not entry.name.startswith('.') and entry.name not in {
                                "$RECYCLE.BIN", "System Volume Information", "Windows",
                                "Program Files", "Program Files (x86)", "ProgramData", "Recovery"}:
                            full = entry.path
                            size = _get_dir_size_recursive(full)
                            size_str = format_size(size) if size > 0 else "0 B"
                            children.append((entry.name, full, size, size_str))
                    except OSError:
                        pass
            except OSError:
                pass

            children.sort(key=lambda x: x[2], reverse=True)
            for name, full, size, size_str in children:
                child = QStandardItem(name)
                child.setData(full, Qt.UserRole)
                child.setData(True, Qt.UserRole + 1)
                sz = QStandardItem(size_str)
                sz.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                cnt = QStandardItem("")
                child.appendRow([QStandardItem("..."), QStandardItem(""), QStandardItem("")])
                item.appendRow([child, sz, cnt])

        threading.Thread(target=_run, daemon=True).start()

    def _on_tree_clicked(self, index):
        """Click on a node with placeholder data triggers expand/lazy load."""
        item = self.tree_model.itemFromIndex(index)
        if item and item.data(Qt.UserRole + 1):
            self.tree_view.expand(index)


def _get_dir_size_recursive(path, depth=0):
    """Recursive size calculation with depth limit."""
    if depth > 20:
        return 0
    total = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if entry.is_file():
                        total += entry.stat().st_size
                    elif entry.is_dir() and not entry.name.startswith('.') and entry.name not in {
                            "$RECYCLE.BIN", "System Volume Information", "Windows"}:
                        total += _get_dir_size_recursive(entry.path, depth + 1)
                except OSError:
                    pass
    except OSError:
        pass
    return total


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
