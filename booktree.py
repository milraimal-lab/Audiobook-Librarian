"""The author/series/book tree widget with drag-drop."""

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
    QComboBox, QSpinBox, QGridLayout, QInputDialog,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QMimeData, QByteArray, QPoint, QTimer
from PyQt6.QtGui  import QPixmap, QAction, QColor, QDrag, QFont, QCursor, QPainter, QPen

import scanner as sc
import tagger as tg
import openlibrary as ol
import audible as au
import organizer as org

from constants import *

def _book_misplacement(book: sc.Book) -> str:
    """
    '' when properly placed, 'author' when the files aren't under a folder
    matching the author, 'series' when they're under the author folder but
    the book has a series tag and no folder level matches it.
    """
    if not book.author or not book.files:
        return ''
    author_norm = org.sanitize(book.author).lower()
    if not author_norm or author_norm == 'unknown':
        return ''

    def folder_parts(af):
        return [org.sanitize(p).lower() for p in af.path.parts[:-1]]

    if not any(author_norm in folder_parts(af) for af in book.files):
        return 'author'

    series = (book.series or '').strip()
    if series:
        series_norm = org.sanitize(series).lower()
        if series_norm and not any(series_norm in folder_parts(af)
                                   for af in book.files):
            return 'series'
    return ''

class BookTreeWidget(QTreeWidget):
    book_selected          = pyqtSignal(object)
    selection_changed      = pyqtSignal(list)   # all selected Book objects
    files_moved            = pyqtSignal()
    books_metadata_changed = pyqtSignal()
    book_created           = pyqtSignal(object) # new sc.Book dropped onto a series node
    books_imported         = pyqtSignal(list)   # book ids that just migrated INTO this tree
    books_organize_requested = pyqtSignal(list) # book ids dropped on author/series nodes

    NODE_AUTHOR = "author"
    NODE_SERIES = "series"
    NODE_BOOK   = "book"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._books: List[sc.Book] = []
        self._filter = ""
        self.book_lookup = None      # callable: bid -> sc.Book or None (cross-tree lookup)
        self.external_titles: set = set()   # lowercase titles living in the OTHER tree
        self._dup_titles: set = set()
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDropIndicatorShown(True)
        self.itemSelectionChanged.connect(self._on_selection_changed)

    def populate(self, books: List[sc.Book], keep_expanded: bool = False):
        expanded = self._collect_expanded() if keep_expanded else set()
        self._books = books
        counts: dict = {}
        for b in books:
            key = ((b.author or '').strip().lower(),
                   (b.title or b.display_name or '').strip().lower())
            if key[1]: counts[key] = counts.get(key, 0) + 1
        self._dup_titles = {k for k, c in counts.items() if c > 1}
        self.blockSignals(True)
        self.clear()
        by_author: dict = {}
        for book in books:
            auth = (book.author or "").strip() or "Unknown Author"
            by_author.setdefault(auth, []).append(book)
        for auth in sorted(by_author, key=str.casefold):
            anode = QTreeWidgetItem(self)
            anode.setText(0, f"📚  {auth}")
            anode.setData(0, Qt.ItemDataRole.UserRole, (self.NODE_AUTHOR, auth))
            anode.setFlags((anode.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                           | Qt.ItemFlag.ItemIsDropEnabled)
            anode.setForeground(0, QColor(LAVENDER))
            f = anode.font(0); f.setBold(True); anode.setFont(0, f)
            by_series: dict = {}
            for book in by_author[auth]:
                series = (book.series or "").strip()
                by_series.setdefault(series, []).append(book)
            for series in sorted(by_series, key=str.casefold):
                if series:
                    snode = QTreeWidgetItem(anode)
                    snode.setText(0, f"📖  {series}")
                    snode.setData(0, Qt.ItemDataRole.UserRole, (self.NODE_SERIES, series))
                    snode.setFlags((snode.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                                   | Qt.ItemFlag.ItemIsDropEnabled)
                    snode.setForeground(0, QColor(PEACH))
                    parent_node = snode
                else:
                    parent_node = anode
                def sort_key(b):
                    try:    return (float(b.series_num or 0), (b.title or "").lower())
                    except: return (999.0, (b.title or "").lower())
                for book in sorted(by_series[series], key=sort_key):
                    self._add_book_node(parent_node, book)
        self.blockSignals(False)
        self.expandAll()
        if keep_expanded: self._restore_expanded(expanded)
        self._apply_filter()

    def _add_book_node(self, parent, book):
        node = QTreeWidgetItem(parent)
        self._refresh_node(node, book); return node

    def add_book(self, book: sc.Book):
        """Insert one book into the live tree without a full rebuild."""
        if book not in self._books:
            self._books.append(book)
        auth   = (book.author or "").strip() or "Unknown Author"
        series = (book.series  or "").strip()

        # Find or create the author node
        anode = self._find_node(self.invisibleRootItem(), self.NODE_AUTHOR, auth)
        if anode is None:
            anode = QTreeWidgetItem(self)
            anode.setText(0, f"📚  {auth}")
            anode.setData(0, Qt.ItemDataRole.UserRole, (self.NODE_AUTHOR, auth))
            anode.setFlags((anode.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                           | Qt.ItemFlag.ItemIsDropEnabled)
            anode.setForeground(0, QColor(LAVENDER))
            f = anode.font(0); f.setBold(True); anode.setFont(0, f)

        # Find or create the series node (if the book has a series)
        if series:
            snode = self._find_node(anode, self.NODE_SERIES, series)
            if snode is None:
                snode = QTreeWidgetItem(anode)
                snode.setText(0, f"📖  {series}")
                snode.setData(0, Qt.ItemDataRole.UserRole, (self.NODE_SERIES, series))
                snode.setFlags((snode.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                               | Qt.ItemFlag.ItemIsDropEnabled)
                snode.setForeground(0, QColor(PEACH))
            parent_node = snode
        else:
            parent_node = anode

        self._add_book_node(parent_node, book)
        anode.setExpanded(True)
        if series: parent_node.setExpanded(True)

        # Hide the new node if it doesn't match the active filter
        tl = self._filter
        if tl:
            self._apply_filter()

    def _find_node(self, parent: QTreeWidgetItem, node_type: str, value: str):
        """Return the first direct child of *parent* matching (node_type, value), or None."""
        for i in range(parent.childCount()):
            child = parent.child(i)
            d = child.data(0, Qt.ItemDataRole.UserRole)
            if d and d[0] == node_type and d[1] == value:
                return child
        return None

    def _refresh_node(self, node, book):
        pos       = f" #{book.series_num}" if book.series_num else ""
        dur       = f"  [{book.duration_str()}]" if book.duration_str() else ""
        files     = f"  ({book.file_count})" if book.file_count else ""
        why       = _book_misplacement(book)
        icon      = "📂" if why else "🎧"
        node.setText(0, f"{icon}  {book.display_name}{pos}{files}{dur}")
        node.setData(0, Qt.ItemDataRole.UserRole, (self.NODE_BOOK, book.id))
        dup_key = ((book.author or '').strip().lower(),
                   (book.title or book.display_name or '').strip().lower())
        dup = bool(dup_key[1]) and (dup_key in self._dup_titles
                                    or dup_key in self.external_titles)
        tip = f"{book.file_count} file(s)  |  {book.author}"
        if why == 'author':
            tip += "\n⚠ Files are not inside the author folder"
        elif why == 'series':
            tip += ("\n⚠ In the author folder but NOT in the series folder — "
                    "right-click → Move to Series Folder")
        if dup:
            tip += "\n⚠ This author has another book with this title (use Find Duplicates)"
        node.setToolTip(0, tip)
        if book.modified:
            color = YELLOW
        elif dup:
            color = RED
        elif why == 'author':
            color = ORANGE
        elif why == 'series':
            color = PEACH
        else:
            color = "#cdd6f4"
        node.setForeground(0, QColor(color))
        node.setFlags(node.flags()
                      | Qt.ItemFlag.ItemIsDragEnabled
                      | Qt.ItemFlag.ItemIsDropEnabled
                      | Qt.ItemFlag.ItemIsSelectable)

    def refresh_book(self, book):
        node = self._find_book_node(book.id)
        if node: self._refresh_node(node, book)

    def _find_book_node(self, bid):
        return self._dfs(self.invisibleRootItem(), bid)

    def _dfs(self, parent, bid):
        for i in range(parent.childCount()):
            child = parent.child(i)
            d = child.data(0, Qt.ItemDataRole.UserRole)
            if d and d[0] == self.NODE_BOOK and d[1] == bid: return child
            found = self._dfs(child, bid)
            if found: return found
        return None

    def apply_filter(self, text):
        self._filter = text.lower(); self._apply_filter()

    # Book fields the filter searches, beyond the node's display text.
    _SEARCH_FIELDS = ('title', 'author', 'narrator', 'series', 'series_num',
                      'year', 'publisher', 'genre')

    def _node_matches(self, book_node, terms, *ancestors) -> bool:
        """True when EVERY search term appears somewhere in the book's
        metadata (title, author, narrator, series, year, publisher, genre …)
        or in its ancestor author/series node text. Multi-term = AND."""
        parts = [book_node.text(0)]
        parts += [a.text(0) for a in ancestors]
        d = book_node.data(0, Qt.ItemDataRole.UserRole)
        if d and d[0] == self.NODE_BOOK:
            b = self._book_by_id(d[1])
            if b:
                parts += [getattr(b, f, '') for f in self._SEARCH_FIELDS]
        blob = ' '.join(str(p) for p in parts if p).lower()
        return all(t in blob for t in terms)

    def _apply_filter(self):
        terms = self._filter.split()          # lowercased in apply_filter()
        root = self.invisibleRootItem()
        for ai in range(root.childCount()):
            anode = root.child(ai); av = False
            for si in range(anode.childCount()):
                child = anode.child(si)
                d = child.data(0, Qt.ItemDataRole.UserRole)
                if d and d[0] == self.NODE_BOOK:
                    # direct book under an author (no series) — include the
                    # author node text so an author search reveals it
                    vis = not terms or self._node_matches(child, terms, anode)
                    child.setHidden(not vis)
                    if vis: av = True
                else:
                    sv = False
                    for bi in range(child.childCount()):
                        bnode = child.child(bi)
                        vis = (not terms
                               or self._node_matches(bnode, terms, anode, child))
                        bnode.setHidden(not vis)
                        if vis: sv = True
                    child.setHidden(not sv)
                    if sv: av = True
            anode.setHidden(not av and bool(terms))

    def _on_selection_changed(self):
        sel = self.selectedItems()
        books = []
        primary = None
        for item in sel:
            d = item.data(0, Qt.ItemDataRole.UserRole)
            if d and d[0] == self.NODE_BOOK:
                b = self._book_by_id(d[1])
                if b:
                    books.append(b)
                    if primary is None: primary = b
        self.book_selected.emit(primary)
        self.selection_changed.emit(books)

    def selected_books(self) -> List[sc.Book]:
        out = []
        for item in self.selectedItems():
            d = item.data(0, Qt.ItemDataRole.UserRole)
            if d and d[0] == self.NODE_BOOK:
                b = self._book_by_id(d[1])
                if b: out.append(b)
        return out

    def select_book(self, book):
        node = self._find_book_node(book.id)
        if node: self.clearSelection(); node.setSelected(True); self.scrollToItem(node)

    def _book_by_id(self, bid):
        return next((b for b in self._books if b.id == bid), None)

    def _resolve_book(self, bid):
        """Find a book by id in this tree, or via the cross-tree lookup."""
        b = self._book_by_id(bid)
        if b is not None: return b
        if self.book_lookup is not None:
            return self.book_lookup(bid)
        return None

    def _collect_expanded(self):
        keys = set(); root = self.invisibleRootItem()
        for ai in range(root.childCount()):
            an = root.child(ai)
            if an.isExpanded(): keys.add(an.text(0))
            for si in range(an.childCount()):
                sn = an.child(si)
                if sn.isExpanded(): keys.add(sn.text(0))
        return keys

    def _restore_expanded(self, keys):
        root = self.invisibleRootItem()
        for ai in range(root.childCount()):
            an = root.child(ai); an.setExpanded(an.text(0) in keys)
            for si in range(an.childCount()):
                sn = an.child(si); sn.setExpanded(sn.text(0) in keys)

    def startDrag(self, supported_actions):
        items = self.selectedItems()
        book_ids = [item.data(0, Qt.ItemDataRole.UserRole)[1]
                    for item in items
                    if item.data(0, Qt.ItemDataRole.UserRole) and
                    item.data(0, Qt.ItemDataRole.UserRole)[0] == self.NODE_BOOK]
        if not book_ids: super().startDrag(supported_actions); return
        mime = QMimeData()
        mime.setData(MIME_BOOKNODES, QByteArray(json.dumps(book_ids).encode()))
        drag = QDrag(self); drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if (event.mimeData().hasFormat(MIME_FILES) or
                event.mimeData().hasFormat(MIME_BOOKNODES)):
            event.acceptProposedAction()
        else: event.ignore()

    def dragMoveEvent(self, event):
        mime = event.mimeData()
        item = self.itemAt(event.position().toPoint())
        if not item:
            # Empty area — allow cross-tree book migration that keeps existing author/series
            if mime.hasFormat(MIME_BOOKNODES):
                event.acceptProposedAction(); return
            event.ignore(); return
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if not d: event.ignore(); return
        if mime.hasFormat(MIME_FILES) and d[0] in (self.NODE_BOOK, self.NODE_SERIES):
            self.setCurrentItem(item); event.acceptProposedAction()
        elif mime.hasFormat(MIME_BOOKNODES) and d[0] in (self.NODE_SERIES, self.NODE_AUTHOR):
            self.setCurrentItem(item); event.acceptProposedAction()
        else: event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        item = self.itemAt(event.position().toPoint())
        if not item:
            # Cross-tree migration onto empty tree area — keep the book's existing
            # author/series untouched; just move it into this tree.
            if mime.hasFormat(MIME_BOOKNODES) and self.book_lookup is not None:
                book_ids = json.loads(bytes(mime.data(MIME_BOOKNODES)).decode())
                imported_ids = []
                for bid in book_ids:
                    if self._book_by_id(bid) is not None: continue  # already mine
                    book = self.book_lookup(bid)
                    if book is None: continue
                    book.modified = True
                    if book not in self._books: self._books.append(book)
                    imported_ids.append(bid)
                if imported_ids:
                    event.acceptProposedAction()
                    self.books_metadata_changed.emit()
                    self.books_imported.emit(imported_ids)
                    return
            event.ignore(); return
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if not d: event.ignore(); return
        if mime.hasFormat(MIME_FILES) and d[0] == self.NODE_BOOK:
            target = self._book_by_id(d[1])
            if not target: event.ignore(); return
            payload = json.loads(bytes(mime.data(MIME_FILES)).decode())
            per_src: dict = {}
            for entry in payload:
                per_src.setdefault(entry["src_book_id"], []).append(entry["file_index"])
            moved = False
            for src_id, indices in per_src.items():
                src = self._resolve_book(src_id)
                if not src or src is target: continue
                to_move = []
                for idx in sorted(set(indices), reverse=True):
                    if 0 <= idx < len(src.files):
                        to_move.insert(0, src.files.pop(idx))
                target.files.extend(to_move)
                src.modified = target.modified = True; moved = True
            if moved: event.acceptProposedAction(); self.files_moved.emit()
            else: event.ignore(); return
        if mime.hasFormat(MIME_FILES) and d[0] == self.NODE_SERIES:
            # Drop files onto a series node → spin them off as a new book in that series
            series = d[1]
            author = ""
            parent = item.parent()
            if parent:
                pd = parent.data(0, Qt.ItemDataRole.UserRole)
                if pd and pd[0] == self.NODE_AUTHOR:
                    author = pd[1]
            payload = json.loads(bytes(mime.data(MIME_FILES)).decode())
            per_src: dict = {}
            for entry in payload:
                per_src.setdefault(entry["src_book_id"], []).append(entry["file_index"])
            collected = []
            for src_id, indices in per_src.items():
                src = self._resolve_book(src_id)
                if not src: continue
                for idx in sorted(set(indices), reverse=True):
                    if 0 <= idx < len(src.files):
                        collected.insert(0, src.files.pop(idx))
                src.modified = True
            if not collected: event.ignore(); return
            new_book = sc.Book()
            new_book.files  = sorted(collected, key=lambda af: af.path.name)
            new_book.series = series
            new_book.author = author
            new_book.title  = collected[0].tags.get('album', '') or collected[0].path.stem
            new_book.modified = True
            self._books.append(new_book)
            event.acceptProposedAction()
            self.book_created.emit(new_book)
            return
        if mime.hasFormat(MIME_BOOKNODES) and d[0] in (self.NODE_SERIES, self.NODE_AUTHOR):
            book_ids = json.loads(bytes(mime.data(MIME_BOOKNODES)).decode())
            changed = False
            imported_ids: list = []
            for bid in book_ids:
                book = self._book_by_id(bid)
                is_external = False
                if book is None and self.book_lookup is not None:
                    book = self.book_lookup(bid)
                    is_external = book is not None
                if not book: continue
                before = (book.author, book.series)
                if d[0] == self.NODE_SERIES:
                    book.series = d[1]
                    parent = item.parent()
                    if parent:
                        pd = parent.data(0, Qt.ItemDataRole.UserRole)
                        if pd and pd[0] == self.NODE_AUTHOR:
                            book.author = pd[1]
                elif d[0] == self.NODE_AUTHOR:
                    book.author = d[1]; book.series = ""
                # Only flag unsaved when the drop really changed something —
                # the organize signal still fires either way so files get filed
                if (book.author, book.series) != before:
                    book.modified = True
                changed = True
                if is_external:
                    if book not in self._books:
                        self._books.append(book)
                    imported_ids.append(bid)
            if changed:
                event.acceptProposedAction()
                self.books_metadata_changed.emit()
                if imported_ids:
                    self.books_imported.emit(imported_ids)
                self.books_organize_requested.emit(book_ids)
            else: event.ignore()
