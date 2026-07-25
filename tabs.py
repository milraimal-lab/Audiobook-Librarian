"""The three main tabs: Edit Metadata, Files, Move/Organise."""

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
    QComboBox, QSpinBox, QGridLayout, QInputDialog, QCompleter,
)
from PyQt6.QtCore import (Qt, QThread, pyqtSignal, QSize, QMimeData, QByteArray,
                          QPoint, QTimer, QStringListModel)
from PyQt6.QtGui  import QPixmap, QAction, QColor, QDrag, QFont, QCursor, QPainter, QPen

import scanner as sc
import tagger as tg
import openlibrary as ol
import audible as au
import organizer as org

from constants import *
from util import parse_audiobook_title, _sanitize, log_line
from workers import FieldSaveThread, OrganizeThread
from dialogs import (RawTagDialog, AddTagDialog, OpenLibraryDialog,
                     CoverViewDialog, SeriesParseDialog, _ClickableLabel)

class EditMetadataTab(QWidget):
    """
    Per-field editing with ▾ tag picker and per-field Apply button.
    Works on Book objects; marks them modified and optionally saves to disk.
    """
    save_books_requested   = pyqtSignal(list)   # list[sc.Book]
    status_message         = pyqtSignal(str)
    tree_refresh_requested = pyqtSignal()
    fields_applied         = pyqtSignal(list)   # books whose folder may be stale

    FIELDS = [
        ("Title",           "title"),
        ("Author",          "author"),
        ("Narrator",        "narrator"),
        ("Series",          "series"),
        ("Series #",        "series_num"),
        ("Year",            "year"),
        ("Publisher",       "publisher"),
        ("Genre",           "genre"),
    ]

    FIELD_TOOLTIPS = {
        'title':      "Book title\n• MP3:     TIT2  (+ TALB album)\n• MP4/M4B: ©nam  (+ ©alb)\n• FLAC/OGG: title (+ album)",
        'author':     "Book author\n• MP3:     TPE1\n• MP4/M4B: ©ART\n• FLAC/OGG: artist",
        'narrator':   "Narrator / reader\n• MP3:     TPE2\n• MP4/M4B: aART\n• FLAC/OGG: performer",
        'series':     "Series name\n• MP3:     TIT1 (grouping)\n• MP4/M4B: ----:com.apple.iTunes:SERIES\n• FLAC/OGG: series",
        'series_num': "Position in series — e.g. 1, 2, 3.5\n• MP3:     TXXX:SERIES_INDEX (+ TXXX:series_num)\n• MP4/M4B: ----:com.apple.iTunes:SERIES-PART\n• FLAC/OGG: series-part",
        'year':       "Publication / release year\n• MP3:     TDRC\n• MP4/M4B: ©day\n• FLAC/OGG: date",
        'publisher':  "Publisher\n• MP3:     TPUB\n• MP4/M4B: ----:com.apple.iTunes:PUBLISHER\n• FLAC/OGG: organization",
        'genre':      "Genre\n• MP3:     TCON\n• MP4/M4B: ©gen\n• FLAC/OGG: genre",
    }
    DESC_TOOLTIP = ("Long-form description / comment\n"
                    "• MP3:     COMM\n"
                    "• MP4/M4B: desc / ©cmt\n"
                    "• FLAC/OGG: description / comment")

    _CSS_COVER_NORMAL  = f"border:1px solid #313244; border-radius:4px; color:{GRAY};"
    _CSS_COVER_PENDING = f"border:2px solid {YELLOW}; border-radius:4px;"

    # Fields that autocomplete from values already in the library
    COMPLETER_FIELDS = ('author', 'narrator', 'series', 'publisher', 'genre')

    def __init__(self, parent=None):
        super().__init__(parent)
        self._books: List[sc.Book] = []
        self._busy = False
        self._field_edits: dict = {}   # key → QLineEdit
        self._completers: dict = {}    # key → (QCompleter, QStringListModel)
        self._extra_edits: list = []   # [(key, original_value, QLineEdit)]
        self._pending_cover: Optional[bytes] = None
        self.all_books_provider = None   # callable → all books (library + import)
        self._build_ui()

    # ── public ────────────────────────────────────────────────────

    def set_books(self, books: List[sc.Book]):
        self._books = books
        self._refresh_completers()
        if books:
            self._load_from_book(books[0])
        else:
            self._clear_fields()
        self._rebuild_extra_tags()
        n = len(books)
        if n == 0:
            self._note_lbl.setText("No books selected.")
        elif n == 1:
            self._note_lbl.setText(f"1 book selected — {books[0].display_name}")
        else:
            self._note_lbl.setText(
                f"{n} books selected. ⚠ The big Save writes ALL fields (Title too!) — "
                "use a field's Apply button to change just that field.")
        self._save_btn.setText(f"Save to {n} Book(s)" if n else "Save to Selected Books")
        self._update_cover_info()

    def _refresh_completers(self):
        """Feed each autocomplete field the distinct values already used across
        the whole collection (library + import)."""
        if not self._completers or not self.all_books_provider:
            return
        all_books = self.all_books_provider() or []
        values: dict = {k: set() for k in self.COMPLETER_FIELDS}
        for b in all_books:
            for key in self.COMPLETER_FIELDS:
                v = (getattr(b, key, '') or '').strip()
                if v:
                    values[key].add(v)
        for key, (comp, model) in self._completers.items():
            model.setStringList(sorted(values[key], key=str.casefold))

    def _update_cover_info(self):
        covers = {c for b in self._books for af in b.files
                  if (c := af.tags.get('cover_art'))}
        if len(covers) > 1:
            self._cover_info_lbl.setText(
                f"🖼 {len(covers)} different file covers — kept on save. "
                "'Apply Cover' replaces them all.")
        else:
            self._cover_info_lbl.setText("")

    # ── build ─────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # ── Top area: cover (left) + fields (right) ───────────────
        top = QHBoxLayout()

        # Left column: note label, cover, action buttons
        left = QVBoxLayout()
        left.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._note_lbl = QLabel("No books selected.")
        self._note_lbl.setStyleSheet(f"color:{GRAY}; font-size:11px;")
        self._note_lbl.setWordWrap(True)
        self._note_lbl.setMaximumWidth(170)
        left.addWidget(self._note_lbl)

        self._cover_lbl = _ClickableLabel("No Cover")
        self._cover_lbl.setFixedSize(160, 160)
        self._cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_lbl.setStyleSheet(self._CSS_COVER_NORMAL)
        self._cover_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cover_lbl.setToolTip("Click to view the cover full-size")
        self._cover_lbl.clicked.connect(self._view_cover_fullsize)
        left.addWidget(self._cover_lbl)

        set_btn = QPushButton("Set Cover…")
        set_btn.setToolTip("Pick an image — it shows as pending (yellow border) until you Apply")
        set_btn.clicked.connect(self._set_cover)
        left.addWidget(set_btn)

        self._apply_cover_btn = QPushButton("Apply Cover")
        self._apply_cover_btn.setEnabled(False)
        self._apply_cover_btn.setToolTip("Write the pending cover to all selected books and save to disk")
        self._apply_cover_btn.clicked.connect(self._apply_cover)
        left.addWidget(self._apply_cover_btn)

        clear_btn = QPushButton("Clear Cover")
        clear_btn.setToolTip("Remove the cover from the selected books (unsaved until Ctrl+S)")
        clear_btn.clicked.connect(self._clear_cover)
        left.addWidget(clear_btn)

        self._cover_info_lbl = QLabel("")
        self._cover_info_lbl.setStyleSheet(f"color:{GRAY}; font-size:10px;")
        self._cover_info_lbl.setWordWrap(True)
        self._cover_info_lbl.setMaximumWidth(170)
        left.addWidget(self._cover_info_lbl)

        self._search_btn = QPushButton("Search Metadata…")
        self._search_btn.setEnabled(False)
        self._search_btn.clicked.connect(self._search_internet)
        left.addWidget(self._search_btn)

        self._parse_series_btn = QPushButton("Parse as Series…")
        self._parse_series_btn.setToolTip(
            "Detect a series across the selected books:\n"
            "the shared name → Series, the number → Series #, the rest → Title.\n"
            "Select 2+ books from the same series, then review before applying.")
        self._parse_series_btn.clicked.connect(self._parse_series)
        left.addWidget(self._parse_series_btn)

        left.addStretch()
        top.addLayout(left)

        # Right column: form fields
        right = QVBoxLayout()

        grid = QGridLayout()
        grid.setSpacing(4)

        for row, (label, key) in enumerate(self.FIELDS):
            lbl = QLabel(label + ":"); lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl.setMinimumWidth(80)
            tip = self.FIELD_TOOLTIPS.get(key, "")
            if tip: lbl.setToolTip(tip)
            grid.addWidget(lbl, row, 0)

            edit = QLineEdit(); self._field_edits[key] = edit
            if tip: edit.setToolTip(tip)
            if key in self.COMPLETER_FIELDS:
                model = QStringListModel(self)
                comp = QCompleter(model, self)
                comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                # MatchContains → "sanderson" matches "Brandon Sanderson"
                comp.setFilterMode(Qt.MatchFlag.MatchContains)
                comp.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
                comp.setMaxVisibleItems(12)
                edit.setCompleter(comp)
                self._completers[key] = (comp, model)
            grid.addWidget(edit, row, 1)

            picker_btn = QPushButton("▾")
            picker_btn.setMaximumWidth(28)
            picker_btn.setToolTip("Pick a value from the file's existing tags")
            picker_btn.clicked.connect(lambda checked, k=key: self._show_tag_picker(k))
            grid.addWidget(picker_btn, row, 2)

            apply_btn = QPushButton("Apply")
            apply_btn.setMaximumWidth(60)
            apply_btn.setToolTip(
                f"Write ONLY {label} to every selected book and save to disk.\n"
                "Safe with multiple books — no other field is touched.")
            apply_btn.clicked.connect(lambda checked, k=key: self._apply_field(k))
            grid.addWidget(apply_btn, row, 3)

            if key == 'title':
                parse_btn = QPushButton("Parse")
                parse_btn.setMaximumWidth(60)
                parse_btn.setToolTip(
                    "Split series info out of the title.\n"
                    'Understands:  "The Inquisition (Summoner, #2)"\n'
                    '"Title (Series, Book 2)"  •  "Series #2 - Title"')
                parse_btn.clicked.connect(self._parse_title)
                grid.addWidget(parse_btn, row, 4)

        # Description row (spans full width, no per-field apply)
        desc_lbl = QLabel("Description:"); desc_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        desc_lbl.setMinimumWidth(80)
        desc_lbl.setToolTip(self.DESC_TOOLTIP)
        grid.addWidget(desc_lbl, len(self.FIELDS), 0)
        self._desc_edit = QTextEdit(); self._desc_edit.setMaximumHeight(70)
        self._desc_edit.setToolTip(self.DESC_TOOLTIP)
        grid.addWidget(self._desc_edit, len(self.FIELDS), 1, 1, 3)

        grid.setColumnStretch(1, 1)
        right.addLayout(grid)

        opt_row = QHBoxLayout()
        self._keep_existing = QCheckBox("Keep existing value when field is blank")
        self._keep_existing.setChecked(True)
        opt_row.addWidget(self._keep_existing)
        opt_row.addStretch()
        self._save_btn = QPushButton("Save to Selected Books")
        self._save_btn.setStyleSheet(BTN_PRIMARY)
        self._save_btn.setEnabled(False)
        self._save_btn.setToolTip("Write every field above to all selected books and save to disk")
        self._save_btn.clicked.connect(self._save_all_fields)
        opt_row.addWidget(self._save_btn)
        right.addLayout(opt_row)
        right.addStretch()

        top.addLayout(right)
        root.addLayout(top)

        # ── Extra tags section ────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#313244;"); root.addWidget(sep)

        xhdr = QHBoxLayout()
        xl = QLabel("Extra tags (not covered above):")
        xl.setStyleSheet(f"color:{BLUE}; font-weight:bold;")
        xhdr.addWidget(xl)
        add_tag_btn = QPushButton("➕ Add Tag")
        add_tag_btn.setToolTip("Add a brand-new tag (custom key + value) to the selected files")
        add_tag_btn.clicked.connect(self._add_extra_tag)
        save_extra_btn = QPushButton("Save Edits")
        save_extra_btn.setStyleSheet(BTN_PRIMARY)
        save_extra_btn.setToolTip("Write edited extra-tag values back to the selected files")
        save_extra_btn.clicked.connect(self._save_extra_edits)
        purge_btn = QPushButton("Purge All Extra Tags")
        purge_btn.setStyleSheet(BTN_ACCENT)
        purge_btn.setToolTip("Removes non-standard tags from selected files (cover art kept)")
        purge_btn.clicked.connect(self._purge_extra_tags)
        xhdr.addStretch(); xhdr.addWidget(add_tag_btn); xhdr.addWidget(save_extra_btn); xhdr.addWidget(purge_btn)
        root.addLayout(xhdr)

        self._extra_scroll = QScrollArea()
        self._extra_scroll.setWidgetResizable(True)
        self._extra_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._extra_scroll.setMaximumHeight(140)
        self._extra_inner = QWidget()
        self._extra_layout = QGridLayout(self._extra_inner)
        self._extra_layout.setSpacing(3)
        self._extra_scroll.setWidget(self._extra_inner)
        root.addWidget(self._extra_scroll)

    # ── field helpers ─────────────────────────────────────────────

    def _load_from_book(self, book: sc.Book):
        self._busy = True
        self._field_edits['title'].setText(book.title)
        self._field_edits['author'].setText(book.author)
        self._field_edits['narrator'].setText(book.narrator)
        self._field_edits['series'].setText(book.series)
        self._field_edits['series_num'].setText(book.series_num)
        self._field_edits['year'].setText(book.year)
        self._field_edits['publisher'].setText(book.publisher)
        self._field_edits['genre'].setText(book.genre)
        self._desc_edit.setPlainText(book.description)
        self._reset_pending_cover()
        self._show_cover(book.cover_art)
        self._save_btn.setEnabled(True)
        self._search_btn.setEnabled(True)
        self._busy = False

    def _clear_fields(self):
        self._busy = True
        for edit in self._field_edits.values(): edit.clear()
        self._reset_pending_cover()
        self._desc_edit.clear(); self._show_cover(None)
        self._save_btn.setEnabled(False); self._search_btn.setEnabled(False)
        self._busy = False

    def _current_values(self) -> dict:
        vals = {k: e.text().strip() for k, e in self._field_edits.items()}
        vals['description'] = self._desc_edit.toPlainText().strip()
        return vals

    # ── cover ─────────────────────────────────────────────────────

    def _show_cover(self, data: Optional[bytes]):
        self._shown_cover_bytes = data
        if data:
            px = QPixmap(); px.loadFromData(data)
            if not px.isNull():
                px = px.scaled(158, 158, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                self._cover_lbl.setPixmap(px); self._cover_lbl.setText(''); return
        self._cover_lbl.clear(); self._cover_lbl.setText("No Cover")

    def _view_cover_fullsize(self):
        data = getattr(self, '_shown_cover_bytes', None)
        if data:
            CoverViewDialog(data, self).exec()

    def _set_cover(self):
        if not self._books: return
        first_file = next((af for b in self._books for af in b.files), None)
        start_dir = str(first_file.path.parent) if first_file else ""
        path, _ = QFileDialog.getOpenFileName(self, "Select Cover Image", start_dir,
            "Images (*.jpg *.jpeg *.png *.webp *.bmp)")
        if not path: return
        self._pending_cover = Path(path).read_bytes()
        self._show_cover(self._pending_cover)
        self._cover_lbl.setStyleSheet(self._CSS_COVER_PENDING)
        self._apply_cover_btn.setEnabled(True)
        self.status_message.emit(
            "Cover is PENDING (yellow border) — click 'Apply Cover' to save it "
            f"to {len(self._books)} book(s).")

    def _apply_cover(self):
        if not self._books or self._pending_cover is None: return
        for book in self._books:
            book.cover_art = self._pending_cover
            book.cover_explicit = True   # overwrite per-file covers on save
            book.modified = True
        n = len(self._books)
        self._reset_pending_cover()
        self.save_books_requested.emit(list(self._books))
        self.status_message.emit(f"Cover applied and saved to {n} book(s).")

    def _clear_cover(self):
        self._reset_pending_cover()
        for book in self._books:
            book.cover_art = None; book.modified = True
        self._show_cover(None)
        self.status_message.emit(
            f"Cover cleared on {len(self._books)} book(s) — unsaved (Ctrl+S to write).")

    def _reset_pending_cover(self):
        self._pending_cover = None
        self._apply_cover_btn.setEnabled(False)
        self._cover_lbl.setStyleSheet(self._CSS_COVER_NORMAL)

    # ── tag picker ────────────────────────────────────────────────

    def _show_tag_picker(self, field_key: str):
        if not self._books: return
        first_file = next((af for b in self._books for af in b.files), None)
        if not first_file: return

        raw = tg.read_all_tags_raw(first_file.path)
        menu = QMenu(self)

        fname_action = menu.addAction(f"Filename:  {first_file.filename}")
        fname_action.setData(first_file.path.stem)

        if raw:
            menu.addSeparator()
            seen = set()
            for k, v in sorted(raw.items()):
                val = str(v).strip()
                if not val or val in seen: continue
                seen.add(val)
                display = val if len(val) <= 60 else val[:57] + "…"
                act = menu.addAction(f"{k}:  {display}")
                act.setData(val)
        else:
            menu.addSeparator()
            menu.addAction("(no tags found)").setEnabled(False)

        # Values from the rest of the collection: for the Author field offer
        # every known author; otherwise offer this author's values from their
        # other books (e.g. pick an existing Series for an untagged book).
        all_books = self.all_books_provider() if self.all_books_provider else []
        book = self._books[0]
        suggestions = []
        if field_key == 'author':
            header = "— all authors in collection —"
            seen_vals = set()
            for b in all_books:
                val = (b.author or '').strip()
                if val and val.lower() not in seen_vals:
                    seen_vals.add(val.lower())
                    suggestions.append(val)
        else:
            author = (book.author or '').strip().lower()
            header = f"— from {book.author}'s other books —" if author else ""
            if author:
                seen_vals = set()
                for b in all_books:
                    if b in self._books: continue
                    if (b.author or '').strip().lower() != author: continue
                    val = str(getattr(b, field_key, '') or '').strip()
                    if val and val.lower() not in seen_vals:
                        seen_vals.add(val.lower())
                        suggestions.append(val)
        if suggestions:
            menu.addSeparator()
            menu.addAction(header).setEnabled(False)
            for val in sorted(suggestions, key=str.casefold)[:25]:
                display = val if len(val) <= 60 else val[:57] + "…"
                act = menu.addAction(f"📚  {display}")
                act.setData(val)

        edit_widget = self._field_edits.get(field_key)
        pos = edit_widget.mapToGlobal(QPoint(0, edit_widget.height())) if edit_widget else QCursor.pos()
        chosen = menu.exec(pos)
        if chosen and chosen.data() is not None:
            self._field_edits[field_key].setText(chosen.data())

    # ── apply / save ──────────────────────────────────────────────

    def _apply_field(self, field_key: str):
        """Write ONE field to every selected book — nothing else is written,
        not even covers."""
        if not self._books:
            QMessageBox.information(self, "No Selection", "Select at least one book first.")
            return
        value = self._field_edits[field_key].text().strip()
        label = field_key.replace('_', ' ').title()
        if not value:
            ans = QMessageBox.question(self, "Clear Field",
                f"The {label} field is empty — REMOVE the {label} tag from "
                f"every file of {len(self._books)} selected book(s)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes: return
        for book in self._books:
            setattr(book, field_key, value)
        done_msg = (f"{label} cleared from" if not value
                    else f"{label} = '{value[:40]}' written to")
        path_relevant  = field_key in ('title', 'author', 'series', 'series_num')
        books_snapshot = list(self._books)

        def _done(n, m=done_msg):
            self.status_message.emit(f"{m} {n} file(s).")
            self.tree_refresh_requested.emit()
            if path_relevant:
                self.fields_applied.emit(books_snapshot)

        self._field_thread = FieldSaveThread(books_snapshot, field_key, value)
        self._field_thread.finished.connect(_done)
        self._field_thread.error.connect(
            lambda e: self.status_message.emit(f"Error writing {label}: {e}"))
        self._field_thread.start()
        self.status_message.emit(f"{'Clearing' if not value else 'Writing'} {label} "
                                 f"on {len(self._books)} book(s)…")

    def _save_all_fields(self):
        if not self._books:
            QMessageBox.information(self, "No Selection", "Select at least one book first.")
            return
        vals = self._current_values()
        keep = self._keep_existing.isChecked()
        for book in self._books:
            for key, value in vals.items():
                if value or not keep:
                    setattr(book, key, value)
            book.modified = True
        self.save_books_requested.emit(list(self._books))
        self.status_message.emit(f"All fields saved to {len(self._books)} book(s).")

    # ── parse title ───────────────────────────────────────────────

    def _parse_title(self):
        if not self._books: return
        raw = self._field_edits['title'].text().strip()
        if not raw: return
        parsed = parse_audiobook_title(raw)
        if len(parsed) <= 1:
            QMessageBox.information(self, "Parse Title",
                'No series pattern detected.\n\nExpects formats like:\n'
                '"The Inquisition (Summoner, #2)"\n"Series #N - Title"')
            return
        if 'title' in parsed:    self._field_edits['title'].setText(parsed['title'])
        if 'series' in parsed and not self._field_edits['series'].text():
            self._field_edits['series'].setText(parsed['series'])
        if 'series_num' in parsed and not self._field_edits['series_num'].text():
            self._field_edits['series_num'].setText(parsed['series_num'])

    def _parse_series(self):
        """Batch-detect a series across the selected books and, after the user
        reviews the preview, write series / number / title to each and save."""
        if len(self._books) < 2:
            QMessageBox.information(self, "Parse as Series",
                "Select two or more books from the same series first.\n\n"
                "The series is detected from the name they share — a single "
                "book has nothing to compare against.")
            return
        dlg = SeriesParseDialog(self._books, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        changed = []
        for book, r in zip(self._books, dlg.get_results()):
            series = (r.get('series') or '').strip()
            if not series:
                continue          # user cleared it → leave this book alone
            book.series     = series
            book.series_num = (r.get('series_num') or '').strip()
            title = (r.get('title') or '').strip()
            if title:
                book.title = title
            book.modified = True
            changed.append(book)
        if not changed:
            self.status_message.emit("Parse as Series — nothing applied.")
            return
        self._load_from_book(self._books[0])
        self.tree_refresh_requested.emit()
        self.save_books_requested.emit(changed)   # only the books we actually touched
        self.status_message.emit(f"Parsed series for {len(changed)} book(s) — saved.")

    # ── open library ──────────────────────────────────────────────

    def _search_internet(self):
        if not self._books: return
        dlg = OpenLibraryDialog(self._books[0], self)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        r = dlg.get_selected()
        if not r: return
        self._busy = True
        for key in COL_KEYS:
            if r.get(key):
                self._field_edits[key].setText(str(r[key]))
        self._busy = False

        # "Apply Picked Fields" commits everything in one go — fields AND
        # cover are written to the selected books, no second Apply needed.
        picked = {k: str(r[k]) for k in COL_KEYS if r.get(k)}
        cover  = r.get('cover_art')
        if not picked and not cover:
            return
        for book in self._books:
            for k, v in picked.items():
                setattr(book, k, v)
            if cover:
                book.cover_art = cover
                book.cover_explicit = True
            book.modified = True
        if cover:
            self._reset_pending_cover()
            self._show_cover(cover)
        self.save_books_requested.emit(list(self._books))
        parts = []
        if picked: parts.append(f"{len(picked)} field(s)")
        if cover:  parts.append("cover")
        self.status_message.emit(
            f"Applied {' + '.join(parts)} to {len(self._books)} book(s) — saving…")

    # ── extra tags ────────────────────────────────────────────────

    def _rebuild_extra_tags(self):
        # Clear existing widgets
        while self._extra_layout.count():
            item = self._extra_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._extra_edits: list = []   # [(key, original_value, QLineEdit)]

        if not self._books: return
        first_file = next((af for b in self._books for af in b.files), None)
        if not first_file: return

        # The Extras panel and Purge share tagger.is_standard_tag — what you
        # see here is exactly what Purge removes.
        raw = tg.read_all_tags_raw(first_file.path)
        extras = [(k, v) for k, v in raw.items()
                  if not tg.is_standard_tag(k) and 'covr' not in k.lower()]

        if not extras:
            lbl = QLabel("No extra tags found.")
            lbl.setStyleSheet(f"color:{GRAY}; font-size:11px;")
            self._extra_layout.addWidget(lbl, 0, 0)
            return

        for row, (k, v) in enumerate(extras):
            klbl = QLabel(k + ":"); klbl.setStyleSheet(f"color:{GRAY}; font-family:Consolas;")
            klbl.setMinimumWidth(200); klbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._extra_layout.addWidget(klbl, row, 0)
            original = str(v)[:200]
            vedit = QLineEdit(original)
            self._extra_layout.addWidget(vedit, row, 1)
            self._extra_edits.append((k, original, vedit))

        self._extra_layout.setColumnStretch(1, 1)

    def _add_extra_tag(self):
        if not self._books:
            QMessageBox.information(self, "No Selection", "Select at least one book first.")
            return
        first_file = next((af for b in self._books for af in b.files), None)
        ext = first_file.ext if first_file else ''
        dlg = AddTagDialog(file_ext=ext, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        key, value = dlg.get()
        if not key:
            QMessageBox.warning(self, "Missing Tag", "Tag key is required.")
            return
        paths = [af.path for b in self._books for af in b.files]
        ok = 0; fail = 0
        for path in paths:
            if tg.add_raw_tag(path, key, value): ok += 1
            else: fail += 1
        self._rebuild_extra_tags()
        msg = f"Added '{key}' = '{value[:30]}{'…' if len(value) > 30 else ''}' to {ok} file(s)."
        if fail: msg += f"  ({fail} failed)"
        self.status_message.emit(msg)

    def _save_extra_edits(self):
        if not self._books:
            self.status_message.emit("No books selected.")
            return
        changed = [(k, e.text()) for (k, orig, e) in self._extra_edits
                   if e.text() != orig]
        if not changed:
            self.status_message.emit("No extra-tag changes to save.")
            return
        paths = [af.path for b in self._books for af in b.files]
        ok = 0; fail = 0
        for path in paths:
            for key, new_val in changed:
                if tg.update_raw_tag(path, key, new_val): ok += 1
                else: fail += 1
        self._rebuild_extra_tags()
        msg = f"Saved {ok} extra-tag write(s) across {len(paths)} file(s)."
        if fail: msg += f"  ({fail} skipped — tag not present in those files)"
        self.status_message.emit(msg)

    def _purge_extra_tags(self):
        if not self._books: return
        paths = [af.path for b in self._books for af in b.files]
        ans = QMessageBox.question(self, "Purge Extra Tags",
            f"Remove non-standard tags from {len(paths)} file(s)?\n"
            "Cover art will be kept. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes: return
        removed = 0
        for path in paths:
            try: removed += len(tg.purge_extra_tags(path))
            except Exception: pass
        self._rebuild_extra_tags()
        self.status_message.emit(f"Purged {removed} extra tag(s) from {len(paths)} file(s).")

class _FileTable(QTableWidget):
    """File table that drags rows out (to the tree) and accepts drops on
    itself for reordering, with a live insertion-line indicator."""
    def __init__(self, owner: 'FilesTab'):
        super().__init__(0, 4)
        self._owner = owner
        self._drop_line = -1   # gap index the line is drawn at; -1 = hidden

    def _mime_ok(self, mime) -> bool:
        return mime.hasFormat(MIME_FILES) or mime.hasFormat(MIME_FILEBLOCK)

    def _gap_for_pos(self, pos) -> int:
        """Insertion gap for a viewport position: 0..rowCount().
        Above a row's midpoint → before it; below → after it."""
        idx = self.indexAt(pos)
        if not idx.isValid():
            return self.rowCount()
        row = idx.row()
        if pos.y() > self.visualRect(idx).center().y():
            row += 1
        return row

    def _set_drop_line(self, gap: int):
        if gap != self._drop_line:
            self._drop_line = gap
            self.viewport().update()

    def startDrag(self, supported_actions):
        self._owner._start_drag(supported_actions)

    def dragEnterEvent(self, event):
        if self._mime_ok(event.mimeData()): event.acceptProposedAction()
        else: super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._mime_ok(event.mimeData()):
            self._set_drop_line(self._gap_for_pos(event.position().toPoint()))
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._set_drop_line(-1)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        gap = self._gap_for_pos(event.position().toPoint())
        self._set_drop_line(-1)
        if event.mimeData().hasFormat(MIME_FILEBLOCK):
            self._owner._drop_block(event, gap)
        elif event.mimeData().hasFormat(MIME_FILES):
            self._owner._drop_reorder(event, gap)
        else:
            super().dropEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_line < 0: return
        if self._drop_line >= self.rowCount():
            if self.rowCount() == 0:
                y = 1
            else:
                y = self.visualRect(self.model().index(self.rowCount() - 1, 0)).bottom() + 1
        else:
            y = self.visualRect(self.model().index(self._drop_line, 0)).top()
        painter = QPainter(self.viewport())
        pen = QPen(QColor(BLUE)); pen.setWidth(2)
        painter.setPen(pen)
        painter.drawLine(0, y, self.viewport().width(), y)

class FilesTab(QWidget):
    """File list + reorder + rename + merge + split, consolidated in one tab."""
    status_message      = pyqtSignal(str)
    merge_requested     = pyqtSignal(list)             # list[sc.Book] to merge
    ops_performed       = pyqtSignal(str, list, bool)  # desc, pairs, is_copy
    build_m4b_requested = pyqtSignal(object)           # list[sc.Book]
    build_m4b_options_requested = pyqtSignal()          # open the M4B options dialog
    split_requested     = pyqtSignal(list)             # [(book, file_idx), …]
    autosplit_requested = pyqtSignal(object)           # sc.Book → split by album tag

    def __init__(self, parent=None):
        super().__init__(parent)
        self.book: Optional[sc.Book] = None
        self._selected_books: List[sc.Book] = []
        self._row_map: list = []      # row → (book, file_idx) or ('sep', book)
        self._collapsed: set = set()  # book ids whose file rows are hidden
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self); lay.setContentsMargins(4, 4, 4, 4)
        hdr = QHBoxLayout()
        self._files_lbl = QLabel("Files in this book")
        self._files_lbl.setStyleSheet(f"color:{GRAY}; font-weight:bold;")
        hdr.addWidget(self._files_lbl)
        hint = QLabel("  Drag rows to reorder  •  drag a ── block ── row to move the whole book  •  drag onto a tree book to move files there")
        hint.setStyleSheet(f"color:{GRAY}; font-size:10px; font-style:italic;")
        hdr.addWidget(hint); hdr.addStretch()
        self._merge_btn = QPushButton("🔗 Merge Selected Books")
        self._merge_btn.setToolTip(
            "Combine all books selected in the tree into the first one.\n"
            "Reorder the files below afterwards, then Save All Tags.")
        self._merge_btn.setEnabled(False)
        self._merge_btn.clicked.connect(
            lambda: self.merge_requested.emit(list(self._selected_books)))
        hdr.addWidget(self._merge_btn)
        self._split_btn = QPushButton("✂ Split")
        self._split_btn.setToolTip(
            "Split a book that was scanned as one but is really several:\n"
            "• Select file rows, then split them into a new book\n"
            "• Or auto-split by each file's album tag")
        split_menu = QMenu(self._split_btn)
        split_menu.addAction("Split selected files into a new book").triggered.connect(
            self._request_split_selected)
        split_menu.addAction("Auto-split this book by album tag").triggered.connect(
            lambda: self.autosplit_requested.emit(self.book))
        self._split_btn.setMenu(split_menu)
        hdr.addWidget(self._split_btn)
        self._m4b_btn = QPushButton("Build M4B…")
        self._m4b_btn.setToolTip(
            "Combine each selected book's files into a single .m4b with chapters.\n"
            "Each file becomes one chapter. Several build in parallel.\n"
            "Requires ffmpeg. Use ⚙ to set where they save and whether to "
            "recycle the originals.")
        self._m4b_btn.clicked.connect(
            lambda: self.build_m4b_requested.emit(self._displayed_books()))
        hdr.addWidget(self._m4b_btn)
        self._m4b_opts_btn = QPushButton("⚙")
        self._m4b_opts_btn.setMaximumWidth(30)
        self._m4b_opts_btn.setToolTip(
            "M4B build options — where the .m4b files save, and whether to move "
            "the originals to the Recycle Bin after building.")
        self._m4b_opts_btn.clicked.connect(
            lambda: self.build_m4b_options_requested.emit())
        hdr.addWidget(self._m4b_opts_btn)
        for lbl2, tip, slot in [
                ("↑ Up",   "", self._move_up),
                ("↓ Down", "", self._move_down),
                ("− All",  "Collapse all book blocks to one line", self._collapse_all),
                ("+ All",  "Expand all book blocks", self._expand_all),
                ("Sort by Filename", "", self._sort_by_filename),
                ("Inspect Tags", "", self._inspect)]:
            b = QPushButton(lbl2); b.clicked.connect(slot)
            if tip: b.setToolTip(tip)
            hdr.addWidget(b)
        lay.addLayout(hdr)

        self.tbl = _FileTable(self)
        self.tbl.setHorizontalHeaderLabels(["#", "Current Filename", "New Name Preview", "Duration"])
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setDragEnabled(True)
        self.tbl.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.tbl.setDropIndicatorShown(True)
        self.tbl.setAutoScroll(True)
        self.tbl.setAutoScrollMargin(50)
        lay.addWidget(self.tbl)

        # ── Rename section ────────────────────────────────────────
        ren_grp = QGroupBox("Rename Files")
        rl = QVBoxLayout(ren_grp)
        pat_row = QHBoxLayout()
        pat_row.addWidget(QLabel("Pattern:"))
        self._pattern_edit = QLineEdit("{title}")
        pat_row.addWidget(self._pattern_edit)
        self._use_counter = QCheckBox("Number files")
        self._use_counter.setChecked(True)
        self._use_counter.setToolTip("Append {n} when the pattern doesn't include it")
        pat_row.addWidget(self._use_counter)
        pat_row.addWidget(QLabel("Start:"))
        self._counter_start = QSpinBox(); self._counter_start.setRange(0, 9999)
        self._counter_start.setValue(1); self._counter_start.setMaximumWidth(70)
        pat_row.addWidget(self._counter_start)
        self._rename_btn = QPushButton("Apply Rename")
        self._rename_btn.setStyleSheet(BTN_PRIMARY)
        self._rename_btn.clicked.connect(self._apply_rename)
        pat_row.addWidget(self._rename_btn)
        rl.addLayout(pat_row)
        hint2 = QLabel("Placeholders: {title}  {author}  {series}  {series_num}  {year}  {narrator}  {n}"
                       "   •   preview in the 'New Name' column above"
                       "   •   single-file books get no number")
        hint2.setStyleSheet(f"color:{GRAY}; font-size:10px;")
        hint2.setWordWrap(True)
        rl.addWidget(hint2)
        lay.addWidget(ren_grp)

        self._pattern_edit.textChanged.connect(self.refresh)
        self._use_counter.stateChanged.connect(self.refresh)
        self._counter_start.valueChanged.connect(self.refresh)

    def set_book(self, book):
        self.book = book
        if len(self._selected_books) < 2:
            self.refresh()

    def set_books(self, books: List[sc.Book]):
        self._selected_books = books or []
        n = len(self._selected_books)
        self._merge_btn.setEnabled(n >= 2)
        self._merge_btn.setText(f"🔗 Merge {n} Selected Books" if n >= 2
                                else "🔗 Merge Selected Books")
        self._rename_btn.setText(f"Apply Rename to {n} Books" if n > 1 else "Apply Rename")
        self._m4b_btn.setText(f"Build {n} M4Bs…" if n > 1 else "Build M4B…")
        self.refresh()

    def _displayed_books(self) -> List[sc.Book]:
        if len(self._selected_books) > 1:
            return self._selected_books
        return [self.book] if self.book else []

    # ── rename helpers ────────────────────────────────────────────

    def _build_new_name(self, pattern: str, book: sc.Book, filename: str,
                        counter: int, pad: int = 1) -> str:
        stem = pattern
        replacements = {
            '{title}':      book.title,
            '{author}':     book.author,
            '{series}':     book.series,
            '{series_num}': book.series_num,
            '{year}':       book.year,
            '{narrator}':   book.narrator,
        }
        for placeholder, value in replacements.items():
            stem = stem.replace(placeholder, value or "")
        if '{n}' in stem:
            stem = stem.replace('{n}', str(counter).zfill(pad))
        return _sanitize(stem) or Path(filename).stem

    def _new_names_for(self, book: sc.Book) -> list:
        """New filename stems for every file of *book*, per the pattern controls."""
        pattern     = self._pattern_edit.text() or "{title}"
        use_counter = self._use_counter.isChecked()
        start       = self._counter_start.value()
        n_files     = len(book.files)
        single      = n_files <= 1
        pad         = len(str(start + n_files - 1)) if n_files else 1
        stems = []
        counter = start
        for af in book.files:
            pat = pattern
            if use_counter and not single and '{n}' not in pat:
                pat += " {n}"
            stems.append(self._build_new_name(pat, book, af.filename, counter, pad))
            counter += 1
        return stems

    def refresh(self):
        self.tbl.clearSpans()
        self.tbl.setRowCount(0)
        self._row_map = []
        books = self._displayed_books()
        multi = len(books) > 1
        if multi:
            self._files_lbl.setText(f"Files in {len(books)} selected books")
        else:
            self._files_lbl.setText("Files in this book")
        for book in books:
            collapsed = book.id in self._collapsed
            if multi:
                r = self.tbl.rowCount(); self.tbl.insertRow(r)
                btn = QPushButton("+" if collapsed else "−")
                btn.setFixedSize(24, 20)
                btn.setStyleSheet("padding:0; font-weight:bold;")
                btn.setToolTip("Collapse / expand this book's files")
                btn.clicked.connect(lambda _, bid=book.id: self._toggle_block(bid))
                self.tbl.setCellWidget(r, 0, btn)
                dur = f"  [{book.duration_str()}]" if book.duration_str() else ""
                sep = QTableWidgetItem(f"{book.display_name}  ({book.file_count} files){dur}")
                sep.setForeground(QColor(PEACH))
                sep.setToolTip("Drag this row to move the whole block — merges follow this order")
                sep.setFlags(Qt.ItemFlag.ItemIsEnabled
                             | Qt.ItemFlag.ItemIsSelectable
                             | Qt.ItemFlag.ItemIsDragEnabled)
                self.tbl.setItem(r, 1, sep)
                self.tbl.setSpan(r, 1, 1, 3)
                self._row_map.append(('sep', book))
                if collapsed:
                    continue
            stems = self._new_names_for(book)
            for i, (af, stem) in enumerate(zip(book.files, stems), 1):
                r = self.tbl.rowCount(); self.tbl.insertRow(r)
                self.tbl.setItem(r, 0, QTableWidgetItem(str(i)))
                self.tbl.setItem(r, 1, QTableWidgetItem(af.filename))
                pi = QTableWidgetItem(stem + af.ext); pi.setForeground(QColor(GREEN))
                self.tbl.setItem(r, 2, pi)
                self.tbl.setItem(r, 3, QTableWidgetItem(af.duration_str()))
                self._row_map.append((book, i - 1))

    def _row_target(self, row: int):
        """Return (book, file_idx) for a table row, or None for separators/invalid."""
        if 0 <= row < len(self._row_map):
            entry = self._row_map[row]
            if entry and entry[0] != 'sep':
                return entry
        return None

    def _row_book(self, row: int):
        """Return the book a row belongs to — works for separator rows too."""
        if 0 <= row < len(self._row_map):
            entry = self._row_map[row]
            if entry:
                return entry[1] if entry[0] == 'sep' else entry[0]
        return None

    def _toggle_block(self, bid: str):
        if bid in self._collapsed: self._collapsed.discard(bid)
        else: self._collapsed.add(bid)
        self.refresh()

    def _collapse_all(self):
        self._collapsed = {b.id for b in self._displayed_books()}
        self.refresh()

    def _expand_all(self):
        self._collapsed.clear()
        self.refresh()

    def _apply_rename(self):
        books = [b for b in self._displayed_books() if b.files]
        if not books:
            QMessageBox.information(self, "Nothing to rename", "Select at least one book first.")
            return

        # Only rename the highlighted file rows when there's a selection;
        # numbering still follows each file's position in the whole book.
        sel = set()
        for idx in self.tbl.selectedIndexes():
            t = self._row_target(idx.row())
            if t: sel.add((id(t[0]), t[1]))

        total_files = len(sel) if sel else sum(b.file_count for b in books)
        if not total_files: return
        scope = (f"{total_files} selected file(s)" if sel else
                 f"all {total_files} file(s) across {len(books)} book(s)")
        ans = QMessageBox.question(self, "Confirm Rename",
            f"Rename {scope}? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes: return

        renamed = 0; errors = []; performed = []
        for book in books:
            for i, (af, stem) in enumerate(zip(book.files, self._new_names_for(book))):
                if sel and (id(book), i) not in sel: continue
                new_path = af.path.parent / (stem + af.ext)
                if new_path == af.path: continue
                try:
                    old = af.path
                    af.path.rename(new_path)
                    af.path = new_path
                    renamed += 1
                    performed.append((old, new_path))
                except Exception as e:
                    errors.append(f"{af.filename}: {e}")

        self.ops_performed.emit(f"Renamed {renamed} file(s)", performed, False)
        if errors:
            QMessageBox.warning(self, "Some renames failed", "\n".join(errors[:10]))
        self.status_message.emit(f"Renamed {renamed} file(s).")
        self.refresh()

    def _request_split_selected(self):
        rows = sorted({i.row() for i in self.tbl.selectedIndexes()})
        pairs = [t for r in rows if (t := self._row_target(r))]
        if not pairs:
            QMessageBox.information(self, "Split",
                "Select one or more file rows first, then split them off.")
            return
        self.split_requested.emit(pairs)

    def _current_af(self):
        t = self._row_target(self.tbl.currentRow())
        return t[0].files[t[1]] if t else None

    def _move_up(self):
        row = self.tbl.currentRow()
        t = self._row_target(row)
        if not t: return
        book, idx = t
        if idx <= 0: return   # already first in its book
        f = book.files; f[idx-1], f[idx] = f[idx], f[idx-1]
        book.modified = True
        self.refresh(); self.tbl.selectRow(row - 1)

    def _move_down(self):
        row = self.tbl.currentRow()
        t = self._row_target(row)
        if not t: return
        book, idx = t
        if idx >= book.file_count - 1: return   # already last in its book
        f = book.files; f[idx], f[idx+1] = f[idx+1], f[idx]
        book.modified = True
        self.refresh(); self.tbl.selectRow(row + 1)

    def _sort_by_filename(self):
        """Natural-sort files by name in every displayed book — handy after merges."""
        sorted_files = 0
        for book in self._displayed_books():
            if book.file_count >= 2:
                book.files.sort(key=lambda af: sc._natural_key(af.path))
                book.modified = True
                sorted_files += book.file_count
        if sorted_files:
            self.refresh()
            self.status_message.emit(f"Sorted {sorted_files} file(s) by filename.")
        else:
            self.status_message.emit("Nothing to sort.")

    def _inspect(self):
        af = self._current_af()
        if af: RawTagDialog(af, self).exec()

    def _start_drag(self, supported_actions):
        rows = sorted({idx.row() for idx in self.tbl.selectedIndexes()})

        # Any separator row in the selection → drag whole block(s)
        block_ids = []
        for r in rows:
            if 0 <= r < len(self._row_map):
                entry = self._row_map[r]
                if entry and entry[0] == 'sep' and entry[1].id not in block_ids:
                    block_ids.append(entry[1].id)
        if block_ids:
            mime = QMimeData()
            mime.setData(MIME_FILEBLOCK, QByteArray(json.dumps(block_ids).encode()))
            drag = QDrag(self.tbl); drag.setMimeData(mime)
            drag.exec(Qt.DropAction.MoveAction)
            return

        payload = []
        for r in rows:
            t = self._row_target(r)
            if t: payload.append({"src_book_id": t[0].id, "file_index": t[1]})
        if not payload: return
        mime = QMimeData()
        mime.setData(MIME_FILES, QByteArray(json.dumps(payload).encode()))
        drag = QDrag(self.tbl); drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def _drop_block(self, event, gap: int):
        """Reorder whole book blocks in the multi-book view. Files stay in
        their books; the merge button follows this displayed order."""
        try:
            block_ids = json.loads(bytes(event.mimeData().data(MIME_FILEBLOCK)).decode())
        except Exception:
            event.ignore(); return
        books = self._selected_books
        moving = [b for b in books if b.id in block_ids]
        if not moving or len(books) < 2:
            event.ignore(); return

        # Which book does the gap anchor to — and before or after it?
        if gap >= len(self._row_map):
            anchor, after = None, True
        else:
            entry = self._row_map[gap]
            if entry[0] == 'sep':               # gap at a block header → before that block
                anchor, after = entry[1], False
            else:
                book, idx = entry               # inside a block: start → before, else after
                anchor, after = book, idx > 0

        remaining = [b for b in books if b.id not in block_ids]
        if anchor is None or anchor.id in block_ids:
            pos = len(remaining)
        else:
            pos = remaining.index(anchor) + (1 if after else 0)
        self._selected_books = remaining[:pos] + moving + remaining[pos:]

        event.acceptProposedAction()
        self.refresh()
        self.status_message.emit(
            "Blocks reordered — merging will combine them in this order.")

    def _drop_reorder(self, event, gap: int):
        """Drop dragged rows back onto the table → reorder (also across books)."""
        try:
            payload = json.loads(bytes(event.mimeData().data(MIME_FILES)).decode())
        except Exception:
            event.ignore(); return
        books = self._displayed_books()
        if not books: event.ignore(); return
        by_id = {b.id: b for b in books}

        # Map the insertion gap to (target book, insertion index within its files)
        if gap >= len(self._row_map):
            tgt_book, insert_idx = books[-1], books[-1].file_count
        else:
            entry = self._row_map[gap]
            if entry[0] == 'sep':
                # Gap right before a block header → end of whatever block sits
                # above it (works for collapsed blocks too); at the very top →
                # start of that header's own block
                prev_book = self._row_book(gap - 1) if gap > 0 else None
                if prev_book is not None:
                    tgt_book, insert_idx = prev_book, prev_book.file_count
                else:
                    tgt_book, insert_idx = entry[1], 0
            else:
                tgt_book, insert_idx = entry

        # Collect dragged (book, index) refs, restricted to displayed books
        per_src: dict = {}
        for entry in payload:
            per_src.setdefault(entry["src_book_id"], []).append(entry["file_index"])
        moving: list = []
        for src_id, indices in per_src.items():
            src = by_id.get(src_id)
            if src is None: continue
            for idx in sorted(set(indices)):
                if 0 <= idx < len(src.files):
                    moving.append((src, idx))
        if not moving: event.ignore(); return

        # Files removed above the insertion point shift it up
        removed_before = sum(1 for src, idx in moving
                             if src is tgt_book and idx < insert_idx)

        # Keep the visual order of the dragged block, then extract (high→low per book)
        display_order = {id(b): i for i, b in enumerate(books)}
        moving.sort(key=lambda t: (display_order.get(id(t[0]), 0), t[1]))
        files_moved = [src.files[idx] for src, idx in moving]
        for src, idx in sorted(moving, key=lambda t: t[1], reverse=True):
            src.files.pop(idx)
            src.modified = True
        tgt_book.modified = True
        insert_at = insert_idx - removed_before
        for offset, af in enumerate(files_moved):
            tgt_book.files.insert(insert_at + offset, af)

        event.acceptProposedAction()
        self.refresh()
        self.status_message.emit(f"Reordered {len(files_moved)} file(s).")

class MoveOrganiseTab(QWidget):
    status_message   = pyqtSignal(str)
    rescan_requested = pyqtSignal()
    ops_performed    = pyqtSignal(str, list, bool)   # desc, pairs, is_copy

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_books: List[sc.Book] = []
        self._all_books:      List[sc.Book] = []
        self._group_rows: list = []   # [{author_cb, series_cb, book_edit, num_edit, preview_lbl, book}]
        self._org_thread: Optional[OrganizeThread] = None
        self._build_ui()

    def set_books(self, selected: List[sc.Book], all_books: List[sc.Book]):
        self._selected_books = selected
        self._all_books = all_books
        self._refresh_groups()

    def set_default_destination(self, folder: str):
        """Pre-fill the destination field with the library folder."""
        if folder:
            self._dest_edit.setText(folder)
            self._update_all_previews()

    def _build_ui(self):
        lay = QVBoxLayout(self); lay.setContentsMargins(8, 8, 8, 8)

        # Destination row
        dest_grp = QGroupBox("File Destination")
        dest_row = QHBoxLayout(dest_grp)
        self._dest_edit = QLineEdit(); self._dest_edit.setPlaceholderText("Select destination folder…")
        dest_row.addWidget(self._dest_edit)
        browse_btn = QPushButton("Browse…"); browse_btn.clicked.connect(self._browse_dest)
        dest_row.addWidget(browse_btn)
        hint = QLabel("Dest / Author / Series / Book")
        hint.setStyleSheet(f"color:{GRAY}; font-size:10px;")
        dest_row.addWidget(hint)
        lay.addWidget(dest_grp)

        # Options row
        opts = QHBoxLayout()
        self._copy_cb = QCheckBox("Copy files (keep originals)")
        self._skip_cb = QCheckBox("Skip if destination already exists")
        self._skip_cb.setToolTip(
            "Ticked: colliding files stay at the source.\n"
            "Unticked: colliding files are auto-renamed like 'Name (2).mp3'.\n"
            "Existing files are NEVER overwritten either way.")
        self._skip_cb.setChecked(True)
        opts.addWidget(self._copy_cb); opts.addWidget(self._skip_cb); opts.addStretch()
        lay.addLayout(opts)

        # Group list header
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Groups  (one row per selected book — updates automatically):"))
        hdr.addStretch()
        move_btn = QPushButton("Move / Copy All Groups")
        move_btn.setStyleSheet(BTN_PRIMARY)
        move_btn.clicked.connect(self._move_files)
        hdr.addWidget(move_btn)
        lay.addLayout(hdr)

        # Column headers
        col_hdr = QWidget()
        col_hdr.setStyleSheet(f"background:#313244;")
        ch = QHBoxLayout(col_hdr); ch.setContentsMargins(4, 2, 4, 2)
        for text, stretch in [("Book", 3), ("Author", 2), ("Series", 2), ("Folder Name", 2), ("#", 1)]:
            l = QLabel(text); l.setStyleSheet(f"color:{BLUE}; font-weight:bold; font-size:11px;")
            ch.addWidget(l, stretch)
        lay.addWidget(col_hdr)

        # Scrollable group rows
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._rows_widget = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setSpacing(2)
        self._rows_layout.addStretch()
        self._scroll.setWidget(self._rows_widget)
        lay.addWidget(self._scroll)

        self._placeholder = QLabel("Select books in a tree — a row appears here for each selected book.")
        self._placeholder.setStyleSheet(f"color:{GRAY}; font-style:italic; padding:12px;")
        self._rows_layout.insertWidget(0, self._placeholder)

    def _browse_dest(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
        if folder:
            self._dest_edit.setText(folder)
            self._update_all_previews()

    def _known_authors(self):
        return sorted({(b.author or "").strip() for b in self._all_books if b.author})

    def _known_series(self):
        return sorted({(b.series or "").strip() for b in self._all_books if b.series})

    def _refresh_groups(self):
        # Remove old rows (keep stretch at end)
        while self._rows_layout.count() > 0:
            item = self._rows_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._group_rows = []

        if not self._selected_books:
            self._placeholder = QLabel("Select books in a tree — a row appears here for each selected book.")
            self._placeholder.setStyleSheet(f"color:{GRAY}; font-style:italic; padding:12px;")
            self._rows_layout.addWidget(self._placeholder)
            self._rows_layout.addStretch()
            return

        authors = self._known_authors()
        series_list = self._known_series()

        for book in self._selected_books:
            row_w = QWidget()
            row_w.setStyleSheet("background:#181825; border-radius:4px;")
            rl = QVBoxLayout(row_w); rl.setContentsMargins(6, 4, 6, 4); rl.setSpacing(2)

            fields_row = QHBoxLayout()

            book_lbl = QLabel(f"🎧 {book.display_name}  ({book.file_count} file(s))")
            book_lbl.setStyleSheet(f"color:{YELLOW};"); book_lbl.setMinimumWidth(160)
            fields_row.addWidget(book_lbl, 3)

            author_cb = QComboBox(); author_cb.setEditable(True)
            author_cb.addItems(authors)
            author_cb.setCurrentText(book.author or "")
            fields_row.addWidget(author_cb, 2)

            series_cb = QComboBox(); series_cb.setEditable(True)
            series_cb.addItems([""] + series_list)
            series_cb.setCurrentText(book.series or "")
            fields_row.addWidget(series_cb, 2)

            folder_edit = QLineEdit(book.title or "")
            fields_row.addWidget(folder_edit, 2)

            num_edit = QLineEdit(book.series_num or "")
            num_edit.setMaximumWidth(50)
            fields_row.addWidget(num_edit, 1)

            rl.addLayout(fields_row)

            preview_lbl = QLabel("")
            preview_lbl.setStyleSheet(f"color:{BLUE}; font-family:Consolas; font-size:10px;")
            preview_lbl.setWordWrap(True)
            rl.addWidget(preview_lbl)

            row_dict = {
                'author': author_cb, 'series': series_cb,
                'folder': folder_edit, 'num': num_edit,
                'preview': preview_lbl, 'book': book,
            }
            self._group_rows.append(row_dict)

            def make_updater(rd):
                def _update(*_):
                    dest = self._dest_edit.text().strip()
                    author = _sanitize(rd['author'].currentText().strip())
                    series = _sanitize(rd['series'].currentText().strip())
                    num    = rd['num'].text().strip()
                    folder = _sanitize(rd['folder'].text().strip())
                    if num and folder:
                        book_folder = f"{num}-{folder}"
                    elif num:
                        book_folder = num
                    else:
                        book_folder = folder
                    if dest and author:
                        parts = [dest, author]
                        if series: parts.append(series)
                        if book_folder: parts.append(book_folder)
                        path_str = str(Path(*parts))
                        rd['preview'].setText("→  " + path_str)
                    else:
                        rd['preview'].setText("")
                return _update

            updater = make_updater(row_dict)
            author_cb.currentTextChanged.connect(updater)
            series_cb.currentTextChanged.connect(updater)
            folder_edit.textChanged.connect(updater)
            num_edit.textChanged.connect(updater)
            self._dest_edit.textChanged.connect(updater)
            updater()

            self._rows_layout.addWidget(row_w)

        self._rows_layout.addStretch()

    def _update_all_previews(self):
        for rd in self._group_rows:
            rd['author'].currentTextChanged.emit(rd['author'].currentText())

    def _move_files(self):
        if not self._group_rows:
            QMessageBox.information(self, "No Groups", "Select one or more books in a tree first.")
            return
        dest = self._dest_edit.text().strip()
        if not dest:
            QMessageBox.warning(self, "No Destination", "Set a destination folder first.")
            return

        # Build move list using organizer
        moves = []
        for rd in self._group_rows:
            book   = rd['book']
            author = _sanitize(rd['author'].currentText().strip()) or 'Unknown Author'
            series = _sanitize(rd['series'].currentText().strip())
            num    = rd['num'].text().strip()
            folder = _sanitize(rd['folder'].text().strip())

            target_dir = org.build_folder_path(dest, author, series, num, folder or book.title)
            total = book.file_count
            for i, af in enumerate(book.files, 1):
                stem = org.build_file_name(folder or book.title, str(i), str(total))
                dst  = target_dir / (stem + af.ext)
                moves.append((af.path, dst))

        copy_mode = self._copy_cb.isChecked()
        skip      = self._skip_cb.isChecked()
        self._org_thread = OrganizeThread(moves, copy_mode, skip)
        self._org_thread.finished.connect(self._on_move_done)
        self._org_thread.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        self._org_thread.start()
        self.status_message.emit(f"{'Copying' if copy_mode else 'Moving'} {len(moves)} files…")

    def _on_move_done(self, done, errors, performed, skipped, renamed):
        copy_mode = self._copy_cb.isChecked()
        if performed:
            self.ops_performed.emit(
                f"{'Copied' if copy_mode else 'Moved'} {len(performed)} file(s)",
                performed, copy_mode)
        for src, dst in renamed:
            log_line(f"RENAMED TO AVOID OVERWRITE: {src} → {dst}")
        for src, dst in skipped:
            log_line(f"SKIPPED (destination exists): {src} → {dst}")
        notes = []
        if skipped:
            notes.append(f"{len(skipped)} skipped — destination already exists:")
            notes += [f"  {d.name}" for _, d in skipped[:5]]
        if renamed:
            notes.append(f"{len(renamed)} auto-renamed to avoid overwriting:")
            notes += [f"  {d.name}" for _, d in renamed[:5]]
        if errors:
            QMessageBox.warning(self, "Completed with Errors",
                f"{done} file(s) processed, {len(errors)} error(s):\n"
                + "\n".join(errors[:8])
                + ("\n\n" + "\n".join(notes) if notes else ""))
        else:
            QMessageBox.information(self, "Done",
                f"{'Copied' if copy_mode else 'Moved'} {len(performed)} file(s)."
                + ("\n\n" + "\n".join(notes) if notes else ""))
        self.status_message.emit(f"Move/copy complete — {len(performed)} file(s).")
        self.rescan_requested.emit()
