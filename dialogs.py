"""Dialogs: metadata search, scan problems, duplicates, import settings, tag tools, cover viewer."""

from pathlib import Path
from typing import Optional, List
import json
import re
import shutil
import sys

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QLabel, QLineEdit, QPushButton, QTextEdit,
    QFormLayout, QScrollArea, QGroupBox, QFileDialog, QMessageBox,
    QDialog, QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QStatusBar, QFrame, QCheckBox, QMenu, QAbstractItemView,
    QToolBar, QTreeWidget, QTreeWidgetItem, QSizePolicy, QTabWidget,
    QComboBox, QSpinBox, QGridLayout, QInputDialog, QRadioButton,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QMimeData, QByteArray, QPoint, QTimer
from PyQt6.QtGui  import QPixmap, QAction, QColor, QDrag, QFont, QCursor, QPainter, QPen

import scanner as sc
import tagger as tg
import openlibrary as ol
import audible as au
import organizer as org

from constants import *
from util import (_suggest_fix, find_ffmpeg, log_line, fmt_size, fmt_duration,
                  parse_series_group, _load_settings, _save_settings)
from workers import SearchThread, CoverFetchThread, RepairThread

class _ClickableLabel(QLabel):
    clicked        = pyqtSignal()
    double_clicked = pyqtSignal()
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)
    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit(); super().mouseDoubleClickEvent(event)

class CoverViewDialog(QDialog):
    """Full-size cover viewer — click anywhere (inside or outside) to close."""
    def __init__(self, data: bytes, parent=None):
        super().__init__(parent)
        # Popup windows close automatically when you click outside them
        self.setWindowFlags(Qt.WindowType.Popup)
        px = QPixmap(); px.loadFromData(data)
        info_txt = f"{px.width()} × {px.height()} px,  {len(data)//1024} KB   —   click to close"
        scr = QApplication.primaryScreen().availableGeometry()
        maxw, maxh = int(scr.width() * 0.8), int(scr.height() * 0.8)
        if px.width() > maxw or px.height() > maxh:
            px = px.scaled(maxw, maxh, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        lay = QVBoxLayout(self)
        lbl = QLabel(); lbl.setPixmap(px)
        lay.addWidget(lbl)
        info = QLabel(info_txt)
        info.setStyleSheet(f"color:{GRAY}; font-size:10px;")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(info)
        self.adjustSize()
        self.move(scr.center() - self.rect().center())

    def mousePressEvent(self, event):
        self.accept()

class OpenLibraryDialog(QDialog):
    def __init__(self, book: sc.Book, parent=None):
        super().__init__(parent)
        self.book = book
        self._results: list = []
        self._covers:  dict = {}
        self._picks:   dict = {}
        self._cover_pick: Optional[bytes] = None
        self._search_thread: Optional[SearchThread]     = None
        self._cover_thread:  Optional[CoverFetchThread] = None
        # Remembered default: seed the query from series+author instead of
        # title+author (handy when the ripped titles are wrong).
        self._by_series = bool(_load_settings().get('search_by_series', False))
        self.setWindowTitle("Search Metadata")
        self.setMinimumSize(1050, 600)
        self._build_ui()
        if self.q_edit.text().strip():
            QTimer.singleShot(0, self._search)

    def _seed_query(self) -> str:
        """The query text seeded from the book: series+author when 'by series'
        is on (and a series exists), else title+author."""
        if self._by_series and (self.book.series or '').strip():
            return f"{self.book.series} {self.book.author}".strip()
        return f"{self.book.title} {self.book.author}".strip()

    def _toggle_by_series(self, state):
        self._by_series = bool(state)
        s = _load_settings(); s['search_by_series'] = self._by_series; _save_settings(s)
        # Reseed the box (only if the user hasn't hand-edited it away)
        self.q_edit.setText(self._seed_query())
        if self.q_edit.text().strip():
            self._search()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        srow = QHBoxLayout()
        self.src_cb = QComboBox()
        self.src_cb.addItems(["Audible", "Open Library"])
        self.src_cb.setToolTip("Audible has narrator + series data; Open Library covers more print books")
        srow.addWidget(self.src_cb)
        self.q_edit = QLineEdit()
        self.q_edit.setPlaceholderText("Search title, author, ISBN …")
        self.q_edit.setText(self._seed_query())
        self.q_edit.returnPressed.connect(self._search)
        srow.addWidget(self.q_edit)
        self._series_cb = QCheckBox("By series")
        self._series_cb.setToolTip(
            "Seed the search from the SERIES + author instead of the title.\n"
            "Useful when the ripped titles are wrong. Remembered as your default.")
        self._series_cb.setChecked(self._by_series)
        self._series_cb.stateChanged.connect(self._toggle_by_series)
        srow.addWidget(self._series_cb)
        self.search_btn = QPushButton("Search")
        self.search_btn.setStyleSheet(BTN_PRIMARY)
        self.search_btn.clicked.connect(self._search)
        srow.addWidget(self.search_btn)
        lay.addLayout(srow)
        self.status_lbl = QLabel("Searching…  •  Double-click any cell to pick that field  •  Click a header to sort  •  Double-click the cover to pick it")
        self.status_lbl.setStyleSheet(f"color:{GRAY}; font-style:italic;")
        lay.addWidget(self.status_lbl)
        hsplit = QSplitter(Qt.Orientation.Horizontal)
        self._filling = False
        self.tbl = QTableWidget(0, len(COL_NAMES) + 1)
        self.tbl.setHorizontalHeaderLabels(["Use"] + COL_NAMES)
        hh = self.tbl.horizontalHeader()
        # Interactive = user-draggable column widths; last column fills the rest
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        for col, wpx in enumerate([40, 280, 150, 140, 140, 40, 60]):
            self.tbl.setColumnWidth(col, wpx)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setWordWrap(True)
        self.tbl.setSortingEnabled(True)   # click a header to sort results
        vh = self.tbl.verticalHeader()
        vh.setVisible(True)
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        vh.setDefaultSectionSize(24)
        self.tbl.cellClicked.connect(self._on_cell_clicked)
        self.tbl.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.tbl.itemChanged.connect(self._on_use_toggled)
        hsplit.addWidget(self.tbl)
        cover_w = QWidget(); cover_w.setFixedWidth(180)
        cl = QVBoxLayout(cover_w); cl.setContentsMargins(4, 0, 0, 0)
        cl.addWidget(QLabel("Cover Preview"))
        self.cover_preview = _ClickableLabel("No Cover")
        self.cover_preview.setFixedSize(165, 210)
        self.cover_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_preview.setStyleSheet(f"border:2px solid #313244; border-radius:4px; color:{GRAY};")
        self.cover_preview.double_clicked.connect(self._pick_cover)
        cl.addWidget(self.cover_preview)
        self.cover_status = QLabel("— no cover —")
        self.cover_status.setStyleSheet(f"color:{GRAY}; font-size:10px;")
        self.cover_status.setWordWrap(True)
        cl.addWidget(self.cover_status)
        pick_btn = QPushButton("✔ Pick This Cover")
        pick_btn.clicked.connect(self._pick_cover); cl.addWidget(pick_btn)
        cl.addStretch(); hsplit.addWidget(cover_w); hsplit.setSizes([700, 180])
        lay.addWidget(hsplit)
        self.picks_lbl = QLabel("No fields picked yet — double-click cells above")
        self.picks_lbl.setStyleSheet(f"color:{GRAY}; font-size:11px;")
        lay.addWidget(self.picks_lbl)
        br = QHBoxLayout()
        clear_btn = QPushButton("Clear All Picks"); clear_btn.clicked.connect(self._clear_picks)
        br.addWidget(clear_btn); br.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        apply  = QPushButton("Apply Picked Fields"); apply.setStyleSheet(BTN_PRIMARY)
        apply.clicked.connect(self.accept)
        br.addWidget(cancel); br.addWidget(apply); lay.addLayout(br)

    def _search(self):
        q = self.q_edit.text().strip()
        if not q: return
        self._stop_cover_thread()
        self.search_btn.setEnabled(False)
        self.status_lbl.setText("Searching…")
        self.tbl.setRowCount(0)
        self._results = []; self._covers = {}; self._picks = {}; self._cover_pick = None
        self._update_cover_preview(None); self._update_picks_label()
        self._search_thread = SearchThread(q, self.src_cb.currentText())
        self._search_thread.finished.connect(self._on_results)
        self._search_thread.error.connect(self._on_search_error)
        self._search_thread.start()

    def _on_results(self, results):
        self._results = results; self.search_btn.setEnabled(True)
        if not results: self.status_lbl.setText("No results found."); return
        self.status_lbl.setText(
            f"{len(results)} result(s)  •  Double-click a cell to pick it  •  "
            "tick 'Use' to pick the whole row")
        self._filling = True
        self.tbl.setSortingEnabled(False)         # don't reorder mid-fill
        self.tbl.setRowCount(0)
        for ri, r in enumerate(results):
            row = self.tbl.rowCount(); self.tbl.insertRow(row)
            use = QTableWidgetItem("")
            use.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                         | Qt.ItemFlag.ItemIsUserCheckable)
            use.setCheckState(Qt.CheckState.Unchecked)
            use.setToolTip("Pick every field of this row (and its cover)")
            use.setData(Qt.ItemDataRole.UserRole, ri)   # survives re-sorting
            self.tbl.setItem(row, 0, use)
            for col, key in enumerate(COL_KEYS):
                self.tbl.setItem(row, col + 1, QTableWidgetItem(str(r.get(key,'') or '')))
        self._filling = False
        self.tbl.setSortingEnabled(True)
        self.tbl.selectRow(0); self._on_cell_clicked(0, 0)
        self._cover_thread = CoverFetchThread(results)
        self._cover_thread.cover_ready.connect(self._on_cover_ready)
        self._cover_thread.start()

    def _result_index(self, row: int) -> int:
        """Map a (possibly re-sorted) table row to its result index, or -1."""
        it = self.tbl.item(row, 0)
        ri = it.data(Qt.ItemDataRole.UserRole) if it else None
        return ri if isinstance(ri, int) else -1

    def _current_result_index(self) -> int:
        return self._result_index(self.tbl.currentRow())

    def _on_search_error(self, msg):
        self.search_btn.setEnabled(True); self.status_lbl.setText(f"Error: {msg}")

    def _stop_cover_thread(self):
        if self._cover_thread and self._cover_thread.isRunning():
            self._cover_thread.stop(); self._cover_thread.wait(500)

    def _on_cover_ready(self, ri, data):
        # ri is the result index (the cover thread walks results in order)
        self._covers[ri] = data
        if self._current_result_index() == ri:
            self._update_cover_preview(data)
            self.cover_status.setText("Cover loaded\nDouble-click image to pick")

    def _on_cell_clicked(self, row, col):
        ri = self._result_index(row)
        data = self._covers.get(ri)
        if data:
            self._update_cover_preview(data)
            self.cover_status.setText("Cover for this result\nDouble-click image or button to pick")
        elif 0 <= ri < len(self._results) and (self._results[ri].get('cover_id')
                                               or self._results[ri].get('cover_url')):
            self._update_cover_preview(None); self.cover_status.setText("Cover loading…")
        else:
            self._update_cover_preview(None); self.cover_status.setText("No cover for this result")

    def _pick_cover(self):
        data = self._covers.get(self._current_result_index())
        if not data: self.cover_status.setText("Cover not loaded yet — wait a moment"); return
        if self._cover_pick == data:
            # Toggle off — picking the same cover again unpicks it
            self._cover_pick = None
            self.cover_preview.setStyleSheet(
                f"border:2px solid #313244; border-radius:4px; color:{GRAY};")
            self.cover_status.setText("Cover unpicked")
            self._update_picks_label()
            return
        self._cover_pick = data
        self.cover_preview.setStyleSheet("border:2px solid #a6e3a1; border-radius:4px;")
        self.cover_status.setText("✔ This cover will be applied (pick again to unpick)")
        self._update_picks_label()

    def _update_cover_preview(self, data: Optional[bytes]):
        if data:
            px = QPixmap(); px.loadFromData(data)
            if not px.isNull():
                px = px.scaled(163, 208, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                self.cover_preview.setPixmap(px); self.cover_preview.setText(''); return
        self.cover_preview.clear(); self.cover_preview.setText("No Cover")

    def _on_cell_double_clicked(self, row, col):
        if col == 0: return           # checkbox column
        logical = col - 1
        item = self.tbl.item(row, col)
        if not item: return
        value = item.text().strip()
        if not value: return
        prev = self._picks.get(logical)
        if prev and prev[0] is not None:
            prev[0].setBackground(QColor("#181825"))
        if prev and prev[0] is item:      # double-clicked the same cell → unpick
            del self._picks[logical]
        else:
            self._picks[logical] = (item, value)   # store the item, not the row
            item.setBackground(QColor(CELL_PICKED))
        self._update_picks_label()

    def _on_use_toggled(self, item):
        """The 'Use' checkbox picks/unpicks every field of its row."""
        if self._filling or item.column() != 0: return
        row = item.row()
        checked = item.checkState() == Qt.CheckState.Checked
        self._filling = True
        # Only one row can be 'used' at a time
        if checked:
            for r in range(self.tbl.rowCount()):
                if r != row:
                    other = self.tbl.item(r, 0)
                    if other and other.checkState() == Qt.CheckState.Checked:
                        other.setCheckState(Qt.CheckState.Unchecked)
        self._filling = False

        # Reset current pick highlights
        for logical, (it, _) in self._picks.items():
            if it is not None: it.setBackground(QColor("#181825"))
        self._picks.clear()

        if checked:
            for logical in range(len(COL_KEYS)):
                cell = self.tbl.item(row, logical + 1)
                val = cell.text().strip() if cell else ''
                if val:
                    self._picks[logical] = (cell, val)
                    cell.setBackground(QColor(CELL_PICKED))
            data = self._covers.get(self._result_index(row))
            if data:
                self._cover_pick = data
                self._update_cover_preview(data)
                self.cover_preview.setStyleSheet("border:2px solid #a6e3a1; border-radius:4px;")
                self.cover_status.setText("✔ This row's cover will be applied")
        else:
            self._cover_pick = None
            self.cover_preview.setStyleSheet(f"border:2px solid #313244; border-radius:4px; color:{GRAY};")
        self._update_picks_label()

    def _clear_picks(self):
        for logical, (it, _) in self._picks.items():
            if it is not None: it.setBackground(QColor("#181825"))
        self._picks.clear(); self._cover_pick = None
        self._filling = True
        for r in range(self.tbl.rowCount()):
            use = self.tbl.item(r, 0)
            if use: use.setCheckState(Qt.CheckState.Unchecked)
        self._filling = False
        self.cover_preview.setStyleSheet(f"border:2px solid #313244; border-radius:4px; color:{GRAY};")
        self._update_picks_label()

    def _update_picks_label(self):
        parts = []
        for col in range(len(COL_NAMES)):
            if col in self._picks:
                _, val = self._picks[col]
                parts.append(f"{COL_NAMES[col]}: '{val[:28]}'")
        if self._cover_pick: parts.append("Cover: ✔ picked")
        if parts: self.picks_lbl.setText("Will apply: " + "  |  ".join(parts))
        else: self.picks_lbl.setText("No fields picked yet — double-click cells above")

    def get_selected(self) -> Optional[dict]:
        out = {}
        for col, key in enumerate(COL_KEYS):
            if col in self._picks: out[key] = self._picks[col][1]
        if self._cover_pick: out['cover_art'] = self._cover_pick
        return out if out else None

    def closeEvent(self, event):
        self._stop_cover_thread(); super().closeEvent(event)

class RawTagDialog(QDialog):
    def __init__(self, af: sc.AudioFile, parent=None):
        super().__init__(parent)
        self.af = af; self.setWindowTitle(f"Raw Tags — {af.filename}")
        self.setMinimumSize(560, 460); self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        self.tbl = QTableWidget(0, 2); self.tbl.setHorizontalHeaderLabels(["Tag", "Value"])
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.verticalHeader().setVisible(False)
        lay.addWidget(self.tbl); self._load()
        br = QHBoxLayout()
        purge = QPushButton("Purge Non-Standard Tags"); purge.clicked.connect(self._purge)
        br.addWidget(purge); br.addStretch()
        close = QPushButton("Close"); close.clicked.connect(self.accept); br.addWidget(close)
        lay.addLayout(br)

    def _load(self):
        self.tbl.setRowCount(0)
        try: tags = tg.read_all_tags_raw(self.af.path)
        except Exception as e: tags = {"error": str(e)}
        for key, val in sorted(tags.items()):
            r = self.tbl.rowCount(); self.tbl.insertRow(r)
            self.tbl.setItem(r, 0, QTableWidgetItem(str(key)))
            self.tbl.setItem(r, 1, QTableWidgetItem(str(val)[:300]))

    def _purge(self):
        ans = QMessageBox.question(self, "Purge Tags",
            "Remove non-standard tags? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            removed = tg.purge_extra_tags(self.af.path)
            QMessageBox.information(self, "Done", f"Removed {len(removed)} tag(s).")
            self._load()

class ProblemsDialog(QDialog):
    """Lists files whose tags failed to read during scanning."""
    def __init__(self, problems: list, parent=None):
        # problems: [(source_label, Path, error_str), …]
        super().__init__(parent)
        self.setWindowTitle("Scan Problems")
        self.setMinimumSize(1180, 420)
        self._repairs: list = []
        lay = QVBoxLayout(self)

        tip = QLabel("These files could not be read properly — usually corrupt or "
                     "mis-named files.  Double-click a row to open its folder.  "
                     "Repair rebuilds the file container losslessly with ffmpeg "
                     "(audio untouched, original goes to the Recycle Bin).")
        tip.setStyleSheet(f"color:{GRAY}; font-size:11px;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(["Source", "File", "Error", "Suggested Fix", ""])
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.setWordWrap(True)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.cellDoubleClicked.connect(self._open_row_folder)
        self._paths: list = []
        for source, path, err in problems:
            r = self.tbl.rowCount(); self.tbl.insertRow(r)
            self.tbl.setItem(r, 0, QTableWidgetItem(source))
            fi = QTableWidgetItem(path.name); fi.setToolTip(str(path))
            self.tbl.setItem(r, 1, fi)
            ei = QTableWidgetItem(err); ei.setForeground(QColor(RED))
            ei.setToolTip(err)
            self.tbl.setItem(r, 2, ei)
            fix = _suggest_fix(err, path)
            si = QTableWidgetItem(fix); si.setForeground(QColor(GREEN))
            si.setToolTip(fix)
            self.tbl.setItem(r, 3, si)
            btn = QPushButton("🔧 Repair")
            junk = path.name.startswith('._') or '__macosx' in str(path).lower()
            if junk:
                btn.setEnabled(False)
                btn.setToolTip("Sidecar junk — just delete it")
            else:
                btn.setToolTip("Lossless ffmpeg remux — original goes to the Recycle Bin")
                btn.clicked.connect(lambda checked=False, row=r: self._repair_row(row))
            self.tbl.setCellWidget(r, 4, btn)
            self._paths.append(path)
        self.tbl.resizeRowsToContents()
        lay.addWidget(self.tbl)

        br = QHBoxLayout(); br.addStretch()
        close = QPushButton("Close"); close.clicked.connect(self.accept)
        br.addWidget(close); lay.addLayout(br)

    def _open_row_folder(self, row, col):
        if 0 <= row < len(self._paths):
            try:
                import os; os.startfile(str(self._paths[row].parent))
            except Exception:
                pass

    def _repair_row(self, row):
        if not (0 <= row < len(self._paths)): return
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            QMessageBox.warning(self, "ffmpeg Not Found",
                "Repair needs ffmpeg — keep ffmpeg.exe next to the app.")
            return
        w = self.tbl.cellWidget(row, 4)
        if w: w.setEnabled(False)
        fx = self.tbl.item(row, 3)
        if fx:
            fx.setText("Repairing (lossless remux)…")
            fx.setForeground(QColor(YELLOW))
        t = RepairThread(self._paths[row], ffmpeg)
        t.done.connect(self._on_repair_done)
        self._repairs.append(t)
        t.start()

    def _on_repair_done(self, path, ok, msg):
        log_line(f"[repair] {path}: {'OK' if ok else 'FAILED'} — {msg}")
        try:
            row = self._paths.index(path)
        except ValueError:
            return
        fx = self.tbl.item(row, 3)
        if fx:
            fx.setText(("✔ " if ok else "✖ ") + msg)
            fx.setForeground(QColor(GREEN if ok else RED))
            fx.setToolTip(msg)
        err_item = self.tbl.item(row, 2)
        if ok and err_item:
            err_item.setForeground(QColor(GRAY))
        w = self.tbl.cellWidget(row, 4)
        if w: w.setEnabled(not ok)   # leave enabled for a retry on failure

class DupResultsDialog(QDialog):
    """Duplicate-check results with per-copy delete buttons."""
    def __init__(self, results: list, delete_cb, parent=None):
        # results: [(verdict, book_a, book_b, stats_a, stats_b)]
        # delete_cb(list[Book]) -> bool
        super().__init__(parent)
        self._delete_cb = delete_cb
        self.setWindowTitle("Duplicate Check Results")
        self.setMinimumSize(1180, 520)
        lay = QVBoxLayout(self)

        tip = QLabel("Each copy shows its total size, runtime and file count — a "
                     "shorter runtime usually means an abridged or truncated copy, "
                     "and the standout value is highlighted green.  Double-click a "
                     "path to open it in Explorer.  Delete moves that copy straight "
                     "to the Recycle Bin — no confirmation, restore from the Bin if "
                     "you change your mind.")
        tip.setStyleSheet(f"color:{GRAY}; font-size:11px;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.tbl = QTableWidget(len(results), 5)
        self.tbl.setHorizontalHeaderLabels(["Verdict", "Book", "Copy A", "Copy B", "Actions"])
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(False)
        for col, wpx in enumerate([210, 210, 300, 300, 175]):
            self.tbl.setColumnWidth(col, wpx)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setWordWrap(True)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.cellDoubleClicked.connect(self._open_in_explorer)
        self._row_paths: list = []   # row → (folder_a, folder_b)

        for r, (verdict, a, b, sa, sb) in enumerate(results):
            vi = QTableWidgetItem(verdict)
            vi.setForeground(QColor(RED if 'EXACT' in verdict else YELLOW))
            vi.setToolTip(verdict)
            self.tbl.setItem(r, 0, vi)
            bi = QTableWidgetItem(f"{a.display_name} — {a.author or 'Unknown'}")
            self.tbl.setItem(r, 1, bi)

            # Flag the standout copy: a longer runtime beats a bigger file,
            # since a short one is usually abridged or incomplete
            longer = larger = None
            if abs(sa['seconds'] - sb['seconds']) > 60:
                longer = 'a' if sa['seconds'] > sb['seconds'] else 'b'
            elif sa['bytes'] and sb['bytes']:
                if abs(sa['bytes'] - sb['bytes']) / max(sa['bytes'], sb['bytes']) > 0.01:
                    larger = 'a' if sa['bytes'] > sb['bytes'] else 'b'

            folders = []
            for col, (book, st, side) in enumerate(
                    ((a, sa, 'a'), (b, sb, 'b')), start=2):
                folder = book.files[0].path.parent
                folders.append(folder)
                note = ('   ◀ longer' if longer == side else
                        '   ◀ larger' if larger == side else '')
                stats = (f"{fmt_size(st['bytes'])}  ·  {fmt_duration(st['seconds'])}"
                         f"  ·  {st['files']} file(s){note}")
                item = QTableWidgetItem(f"{stats}\n{folder}")
                if note:
                    item.setForeground(QColor(GREEN))
                item.setToolTip(f"{stats}\n{folder}\n(double-click to open in Explorer)")
                self.tbl.setItem(r, col, item)
            self._row_paths.append(tuple(folders))

            w = QWidget(); wl = QHBoxLayout(w)
            wl.setContentsMargins(2, 0, 2, 0); wl.setSpacing(4)
            for label, book in (("Delete A", a), ("Delete B", b)):
                btn = QPushButton(label)
                btn.setStyleSheet(BTN_ACCENT)
                btn.clicked.connect(
                    lambda checked=False, bk=book, row=r: self._delete(bk, row))
                wl.addWidget(btn)
            self.tbl.setCellWidget(r, 4, w)

        self.tbl.resizeRowsToContents()
        lay.addWidget(self.tbl)

        br = QHBoxLayout(); br.addStretch()
        close = QPushButton("Close"); close.clicked.connect(self.accept)
        br.addWidget(close); lay.addLayout(br)

    def _open_in_explorer(self, row, col):
        """Double-click on a Copy A / Copy B path opens that folder."""
        if col not in (2, 3) or not (0 <= row < len(self._row_paths)):
            return
        folder = self._row_paths[row][col - 2]
        try:
            import os; os.startfile(str(folder))
        except Exception:
            pass

    def _delete(self, book, row):
        if not self._delete_cb([book]):
            return
        for c in range(4):
            it = self.tbl.item(row, c)
            if it: it.setForeground(QColor(GRAY))
        vi = self.tbl.item(row, 0)
        if vi: vi.setText(f"♻ recycled '{book.display_name}' copy")
        w = self.tbl.cellWidget(row, 4)
        if w: w.setEnabled(False)

class ImportSettingsDialog(QDialog):
    def __init__(self, enabled: bool, folder: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Folder")
        self.setMinimumWidth(540)
        lay = QVBoxLayout(self)

        tip = QLabel(
            "The Import folder is a separate scan source for new or unsorted books.\n"
            "When enabled, its contents appear in a second tree above the Library tree.\n"
            "Files are NOT moved automatically — use the Move / Organise tab to relocate them\n"
            "into the Library folder."
        )
        tip.setStyleSheet(f"color:{GRAY}; font-size:11px;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.enable_cb = QCheckBox("Enable Import folder")
        self.enable_cb.setChecked(enabled)
        lay.addWidget(self.enable_cb)

        row = QHBoxLayout()
        row.addWidget(QLabel("Folder:"))
        self.path_edit = QLineEdit(folder)
        self.path_edit.setPlaceholderText("Select a folder to scan for unsorted books…")
        row.addWidget(self.path_edit)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        lay.addLayout(row)

        br = QHBoxLayout(); br.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        ok = QPushButton("OK"); ok.setStyleSheet(BTN_PRIMARY); ok.clicked.connect(self.accept)
        br.addWidget(cancel); br.addWidget(ok)
        lay.addLayout(br)

    def _browse(self):
        start = self.path_edit.text().strip() or str(Path.home())
        f = QFileDialog.getExistingDirectory(self, "Select Import Folder", start)
        if f: self.path_edit.setText(f)

    def get(self) -> tuple:
        return self.enable_cb.isChecked(), self.path_edit.text().strip()

class AddTagDialog(QDialog):
    """Add a new extra tag to the selected file(s)."""

    # Suggested tag names — picking one fills in a per-format default
    SUGGESTIONS = [
        # (display_name, mp3_key,           mp4_key,                            vorbis_key)
        ("ASIN",       "TXXX:ASIN",       "----:com.apple.iTunes:ASIN",       "asin"),
        ("ISBN",       "TXXX:ISBN",       "----:com.apple.iTunes:ISBN",       "isbn"),
        ("Subtitle",   "TIT3",            "----:com.apple.iTunes:SUBTITLE",   "subtitle"),
        ("Composer",   "TCOM",            "\xa9wrt",                          "composer"),
        ("Copyright",  "TCOP",            "cprt",                             "copyright"),
        ("Language",   "TLAN",            "----:com.apple.iTunes:LANGUAGE",   "language"),
        ("Mood",       "TMOO",            "----:com.apple.iTunes:MOOD",       "mood"),
        ("Rating",     "TXXX:RATING",     "----:com.apple.iTunes:RATING",     "rating"),
        ("Website",    "WOAS",            "----:com.apple.iTunes:WEBSITE",    "website"),
    ]

    def __init__(self, file_ext: str = '', parent=None):
        super().__init__(parent)
        self.file_ext = file_ext.lower()
        self.setWindowTitle("Add Extra Tag")
        self.setMinimumWidth(580)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)

        tip = QLabel(
            "Add a new tag to all selected file(s).  Pick a common tag from the "
            "dropdown (auto-filled for your file format) or type a custom key.\n\n"
            "Format examples:\n"
            "• MP3 (ID3):     TXXX:MYTAG     or     TIT3\n"
            "• MP4 (M4B):    ----:com.apple.iTunes:MYTAG     or     ©nam\n"
            "• FLAC / OGG:  mytag  (lowercase)")
        tip.setStyleSheet(f"color:{GRAY}; font-size:11px;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # Quick-pick row
        qp = QHBoxLayout()
        qp.addWidget(QLabel("Quick pick:"))
        self.suggest_cb = QComboBox()
        self.suggest_cb.addItem("— custom —")
        for name, *_ in self.SUGGESTIONS:
            self.suggest_cb.addItem(name)
        self.suggest_cb.currentIndexChanged.connect(self._apply_suggestion)
        qp.addWidget(self.suggest_cb); qp.addStretch()
        lay.addLayout(qp)

        # Tag row
        row1 = QHBoxLayout()
        l1 = QLabel("Tag:"); l1.setMinimumWidth(60); row1.addWidget(l1)
        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("e.g. TXXX:ASIN  •  ----:com.apple.iTunes:ASIN  •  asin")
        row1.addWidget(self.tag_edit)
        lay.addLayout(row1)

        # Value row
        row2 = QHBoxLayout()
        l2 = QLabel("Value:"); l2.setMinimumWidth(60); row2.addWidget(l2)
        self.value_edit = QLineEdit()
        self.value_edit.setPlaceholderText("Value to store")
        row2.addWidget(self.value_edit)
        lay.addLayout(row2)

        br = QHBoxLayout(); br.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        ok = QPushButton("Add Tag"); ok.setStyleSheet(BTN_PRIMARY)
        ok.clicked.connect(self.accept)
        br.addWidget(cancel); br.addWidget(ok)
        lay.addLayout(br)

    def _apply_suggestion(self, idx):
        if idx <= 0: return
        _, mp3_k, mp4_k, vorb_k = self.SUGGESTIONS[idx - 1]
        if self.file_ext == '.mp3':
            self.tag_edit.setText(mp3_k)
        elif self.file_ext in ('.m4b', '.m4a', '.aac', '.mp4', '.alac'):
            self.tag_edit.setText(mp4_k)
        elif self.file_ext in ('.flac', '.ogg', '.opus'):
            self.tag_edit.setText(vorb_k)
        else:
            self.tag_edit.setText(vorb_k)

    def get(self) -> tuple:
        return self.tag_edit.text().strip(), self.value_edit.text().strip()

class M4bBuildDialog(QDialog):
    """Where built M4Bs are saved, whether the source files are recycled after
    a clean build, and whether to remember the answer so it stops asking."""

    def __init__(self, own_folder=True, folder='', recycle=False,
                 remember=False, ok_label="Build", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Build M4B — Options")
        self.setMinimumWidth(540)
        self._build_ui(own_folder, folder, recycle, remember, ok_label)

    def _build_ui(self, own_folder, folder, recycle, remember, ok_label):
        lay = QVBoxLayout(self)

        q = QLabel("Where should the .m4b files be saved?")
        q.setStyleSheet("font-weight:bold;")
        lay.addWidget(q)

        self._own_rb = QRadioButton("In each book's own folder  (next to its source files)")
        self._one_rb = QRadioButton("All in one folder:")
        self._own_rb.setChecked(own_folder)
        self._one_rb.setChecked(not own_folder)
        lay.addWidget(self._own_rb)

        row = QHBoxLayout()
        row.addWidget(self._one_rb)
        self._folder_edit = QLineEdit(folder)
        self._folder_edit.setPlaceholderText("Choose a folder…")
        browse = QPushButton("Browse…"); browse.clicked.connect(self._browse)
        row.addWidget(self._folder_edit, 1); row.addWidget(browse)
        lay.addLayout(row)
        self._own_rb.toggled.connect(self._sync_enabled)
        self._sync_enabled()

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#313244;"); lay.addWidget(sep)

        self._recycle_cb = QCheckBox("Move each book's source files to the Recycle Bin after it builds")
        self._recycle_cb.setChecked(recycle)
        self._recycle_cb.setToolTip(
            "Only files that build without error are removed. The new .m4b is "
            "always kept, and files go to the Recycle Bin so they're restorable.")
        lay.addWidget(self._recycle_cb)

        self._remember_cb = QCheckBox("Remember these settings and don't ask again")
        self._remember_cb.setChecked(remember)
        self._remember_cb.setToolTip(
            "Future builds use these settings silently. Reopen this dialog any "
            "time with the ⚙ button next to Build M4B.")
        lay.addWidget(self._remember_cb)

        br = QHBoxLayout(); br.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        ok = QPushButton(ok_label); ok.setStyleSheet(BTN_PRIMARY)
        ok.clicked.connect(self._on_ok)
        br.addWidget(cancel); br.addWidget(ok)
        lay.addLayout(br)

    def _sync_enabled(self):
        self._folder_edit.setEnabled(self._one_rb.isChecked())

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose output folder",
                                             self._folder_edit.text().strip() or "")
        if d:
            self._folder_edit.setText(d); self._one_rb.setChecked(True)

    def _on_ok(self):
        if self._one_rb.isChecked() and not self._folder_edit.text().strip():
            QMessageBox.warning(self, "No Folder",
                "Pick a folder, or choose “each book's own folder”.")
            return
        self.accept()

    def get(self) -> tuple:
        """(own_folder: bool, folder: str, recycle: bool, remember: bool)"""
        return (self._own_rb.isChecked(), self._folder_edit.text().strip(),
                self._recycle_cb.isChecked(), self._remember_cb.isChecked())

class SeriesParseDialog(QDialog):
    """Batch 'Parse as Series': infer series / number / title for a set of
    books from their shared name prefix, then preview and edit before applying.
    """

    def __init__(self, books, parent=None):
        super().__init__(parent)
        self.books = list(books)
        self.setWindowTitle("Parse as Series")
        self.setMinimumSize(780, 460)
        self._results: list = []
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)

        tip = QLabel(
            "Books that share a leading name are treated as one series: the "
            "shared part becomes the Series, the number becomes the Series #, "
            "and whatever's left becomes the Title.\n"
            "Every cell is editable — fix anything the guesser got wrong. "
            "Clear a row's Series cell to leave that book untouched.")
        tip.setStyleSheet(f"color:{GRAY}; font-size:11px;"); tip.setWordWrap(True)
        lay.addWidget(tip)

        names  = [b.title or b.display_name for b in self.books]
        parsed = parse_series_group(names)

        self.table = QTableWidget(len(self.books), 4)
        self.table.setHorizontalHeaderLabels(["Book (original)", "Series", "#", "Title"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        for row, (name, p) in enumerate(zip(names, parsed)):
            orig = QTableWidgetItem(name)
            orig.setFlags(orig.flags() & ~Qt.ItemFlag.ItemIsEditable)
            orig.setForeground(QColor(GRAY))
            self.table.setItem(row, 0, orig)
            self.table.setItem(row, 1, QTableWidgetItem(p.get('series', '')))
            self.table.setItem(row, 2, QTableWidgetItem(p.get('series_num', '')))
            self.table.setItem(row, 3, QTableWidgetItem(p.get('title', name)))
        lay.addWidget(self.table)

        detected = sum(1 for p in parsed if p.get('series'))
        info = QLabel(f"Detected a series for {detected} of {len(self.books)} book(s).")
        info.setStyleSheet(f"color:{BLUE}; font-size:11px;")
        lay.addWidget(info)

        br = QHBoxLayout(); br.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        ok = QPushButton(f"Apply to {len(self.books)} Book(s)")
        ok.setStyleSheet(BTN_PRIMARY); ok.clicked.connect(self._on_apply)
        br.addWidget(cancel); br.addWidget(ok)
        lay.addLayout(br)

    def _on_apply(self):
        self._results = []
        for row in range(self.table.rowCount()):
            self._results.append({
                'series':     self.table.item(row, 1).text().strip(),
                'series_num': self.table.item(row, 2).text().strip(),
                'title':      self.table.item(row, 3).text().strip(),
            })
        self.accept()

    def get_results(self) -> list:
        """List aligned with `books`: {'series','series_num','title'} per row."""
        return self._results
