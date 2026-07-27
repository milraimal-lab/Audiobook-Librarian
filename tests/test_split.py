"""Splitting a book apart in the Files tab.

Regression guard: Book is a dataclass with eq=True, so Python sets
__hash__ = None and the object cannot be used as a dict key. Grouping the
selected rows by Book crashed with TypeError; grouping by book.id is the fix.
"""
import os
import tempfile
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
    """A fully isolated MainWindow: no remembered folders (so it never scans a
    real library), no writes to the user's settings file, and no modal dialogs
    (which would block forever with no event loop running)."""
    import mainwindow as mw
    monkeypatch.setattr(mw, '_load_settings', lambda: {})
    monkeypatch.setattr(mw, '_save_settings', lambda d: None)
    monkeypatch.setattr(QMessageBox, 'information', staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, 'warning', staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    w = mw.MainWindow()
    w.books.clear()
    w.import_books.clear()
    yield w
    # Clear the unsaved flags so closing can't stall on a confirmation
    for b in w.books + w.import_books:
        b.modified = False
    w.close()


def _book(tmp: Path, title, n_files, album_every=5, **kw):
    b = sc.Book()
    b.title = title
    for k, v in kw.items():
        setattr(b, k, v)
    for i in range(n_files):
        p = tmp / f'{title} {i + 1:02d}.mp3'
        p.write_bytes(b'x')
        b.files.append(sc.AudioFile(
            path=p, tags={'album': f'Volume {1 + i // album_every}'}, hydrated=True))
    return b


def test_book_is_unhashable():
    """The property that caused the crash — documented so it isn't forgotten."""
    assert sc.Book.__hash__ is None
    with pytest.raises(TypeError):
        {sc.Book(): 1}


def test_split_selected_files(win, tmp_path):
    book = _book(tmp_path, 'White Sand', 12,
                 author='Brandon Sanderson', series='White Sand')
    win.books.append(book)

    # Rows 6-10 in the UI → zero-based 5..9
    win._split_files_to_new_book([(book, i) for i in range(5, 10)])

    assert len(win.books) == 2
    orig, new = win.books[0], win.books[1]
    assert orig.file_count == 7
    assert new.file_count == 5
    assert [f.path.name for f in new.files] == [
        f'White Sand {i:02d}.mp3' for i in range(6, 11)]


def test_split_inherits_and_refines_metadata(win, tmp_path):
    book = _book(tmp_path, 'White Sand', 12,
                 author='Brandon Sanderson', series='White Sand', year='2016')
    win.books.append(book)
    win._split_files_to_new_book([(book, i) for i in range(5, 10)])
    new = win.books[1]
    assert new.author == 'Brandon Sanderson'   # inherited from the source
    assert new.series == 'White Sand'
    assert new.title == 'Volume 2'             # refined from its own album tag
    assert new.modified and book.modified


def test_split_every_file_is_refused(win, tmp_path):
    book = _book(tmp_path, 'Solo', 3)
    win.books.append(book)
    win._split_files_to_new_book([(book, i) for i in range(3)])
    assert len(win.books) == 1        # nothing split off
    assert book.file_count == 3


def test_split_across_two_books_at_once(win, tmp_path):
    (tmp_path / 'a').mkdir(exist_ok=True)
    (tmp_path / 'b').mkdir(exist_ok=True)
    a = _book(tmp_path / 'a', 'Alpha', 4)
    b = _book(tmp_path / 'b', 'Beta', 4)
    win.books.extend([a, b])
    win._split_files_to_new_book([(a, 0), (a, 1), (b, 0)])
    assert len(win.books) == 4        # two sources + two new books
    assert a.file_count == 2 and b.file_count == 3


def test_split_from_import_tree_stays_in_import(win, tmp_path):
    book = _book(tmp_path, 'Imported', 6)
    win.import_books.append(book)
    win._split_files_to_new_book([(book, 4), (book, 5)])
    assert len(win.import_books) == 2
    assert not win.books           # nothing leaked into the library list


# ── Split-each: every selected file → its own single-file book ──

def test_split_each_all_files_removes_source(win, tmp_path):
    book = _book(tmp_path, 'Anthology', 5, author='Various')
    win.books.append(book)
    win._split_files_each_to_own_book([(book, i) for i in range(5)])
    assert len(win.books) == 5              # source replaced by 5 books
    assert book not in win.books            # emptied source dropped
    assert all(b.file_count == 1 for b in win.books)
    assert all(b.author == 'Various' for b in win.books)          # inherited
    assert sorted(b.title for b in win.books) == [
        f'Anthology {i:02d}' for i in range(1, 6)]                # titled per file

def test_split_each_subset_keeps_source(win, tmp_path):
    book = _book(tmp_path, 'Mixed', 6)
    win.books.append(book)
    win._split_files_each_to_own_book([(book, 0), (book, 1)])
    assert len(win.books) == 3              # source (now 4 files) + 2 singles
    assert book in win.books and book.file_count == 4
    assert all(b.file_count == 1 for b in win.books if b is not book)

def test_split_each_from_import_stays_in_import(win, tmp_path):
    book = _book(tmp_path, 'Imp', 3)
    win.import_books.append(book)
    win._split_files_each_to_own_book([(book, 0), (book, 1), (book, 2)])
    assert len(win.import_books) == 3 and not win.books

def test_split_each_declined_does_nothing(win, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    book = _book(tmp_path, 'Keep', 4)
    win.books.append(book)
    win._split_files_each_to_own_book([(book, 0), (book, 1)])
    assert len(win.books) == 1 and book.file_count == 4          # unchanged
