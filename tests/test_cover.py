"""Cover-based features: auto-split by cover art, and the Files-tab
group-by-cover view."""
import os
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import scanner as sc

pytest.importorskip('PyQt6.QtWidgets')
from PyQt6.QtWidgets import QApplication, QMessageBox


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, monkeypatch):
    import mainwindow as mw
    monkeypatch.setattr(mw, '_load_settings', lambda: {})
    monkeypatch.setattr(mw, '_save_settings', lambda d: None)
    monkeypatch.setattr(QMessageBox, 'information', staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, 'warning', staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    w = mw.MainWindow()
    w.books.clear(); w.import_books.clear()
    yield w
    for b in w.books + w.import_books:
        b.modified = False
    w.close()


def _book_with_covers(tmp: Path, title: str, covers: list):
    """One file per entry in *covers*; each entry is the cover bytes (or None)."""
    b = sc.Book(); b.title = title; b.author = 'A'
    for i, cov in enumerate(covers):
        p = tmp / f'{title} {i + 1:02d}.mp3'
        p.write_bytes(b'x')
        tags = {'album': 'X'}
        if cov is not None:
            tags['cover_art'] = cov
        b.files.append(sc.AudioFile(path=p, tags=tags, hydrated=True))
    return b


# ── auto-split by cover ──────────────────────────────────────────

def test_autosplit_by_cover_groups(win, tmp_path):
    A, B = b'coverA' * 60, b'coverB' * 60
    book = _book_with_covers(tmp_path, 'Glued', [A, A, B, B, B])
    win.books.append(book)
    win._autosplit_by_cover(book)
    assert len(win.books) == 2                       # two cover groups
    # original keeps the first group (the two A files)
    assert book.file_count == 2
    other = next(b for b in win.books if b is not book)
    assert other.file_count == 3

def test_autosplit_by_cover_single_cover_noop(win, tmp_path):
    A = b'samecover' * 40
    book = _book_with_covers(tmp_path, 'One', [A, A, A])
    win.books.append(book)
    win._autosplit_by_cover(book)
    assert len(win.books) == 1 and book.file_count == 3   # nothing split

def test_autosplit_by_cover_no_cover_vs_cover(win, tmp_path):
    A = b'hascover' * 40
    book = _book_with_covers(tmp_path, 'Mix', [A, None, A, None])
    win.books.append(book)
    win._autosplit_by_cover(book)
    assert len(win.books) == 2                       # 'has cover' vs 'no cover'


# ── Files-tab group-by-cover view ────────────────────────────────

def test_files_tab_group_by_cover_row_map(qapp, tmp_path):
    from tabs import FilesTab
    A, B = b'coverA' * 60, b'coverB' * 60
    book = _book_with_covers(tmp_path, 'View', [A, A, B])
    tab = FilesTab()
    tab.set_book(book)
    tab._toggle_group_by_cover(True)
    # two cover-group header rows ('csep') + three file rows
    headers = [e for e in tab._row_map if e and isinstance(e[0], str)]
    files   = [e for e in tab._row_map if e and not isinstance(e[0], str)]
    assert len(headers) == 2 and all(e[0] == 'csep' for e in headers)
    assert len(files) == 3
    # collapsing one cover group hides its file rows
    from util import cover_key
    tab._toggle_cover(cover_key(A))
    files_after = [e for e in tab._row_map if e and not isinstance(e[0], str)]
    assert len(files_after) == 1                     # only cover-B's file remains

def test_files_tab_row_target_ignores_cover_headers(qapp, tmp_path):
    from tabs import FilesTab
    A, B = b'cA' * 60, b'cB' * 60
    book = _book_with_covers(tmp_path, 'V2', [A, B])
    tab = FilesTab()
    tab.set_book(book)
    tab._toggle_group_by_cover(True)
    # every header row maps to no file; every file row maps to a real (book, idx)
    for row, entry in enumerate(tab._row_map):
        t = tab._row_target(row)
        if isinstance(entry[0], str):
            assert t is None
        else:
            assert t is not None and t[0] is book
