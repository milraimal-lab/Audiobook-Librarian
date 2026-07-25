"""Library tree filter: searches all metadata, not just titles.

Regression guard for 'search by more than titles' — the old filter only
matched the book node's display text, so author/series/narrator searches
returned nothing.
"""
import os
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import scanner as sc

pytest.importorskip('PyQt6.QtWidgets')
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def _book(title, author='', series='', series_num='', narrator='',
          year='', publisher='', genre=''):
    b = sc.Book()
    b.title, b.author, b.series = title, author, series
    b.series_num, b.narrator = series_num, narrator
    b.year, b.publisher, b.genre = year, publisher, genre
    b.files.append(sc.AudioFile(path=Path(f'{title}.mp3')))
    return b


LIBRARY = [
    _book('The Way of Kings', 'Brandon Sanderson', 'The Stormlight Archive', '1',
          narrator='Michael Kramer', year='2010', publisher='Tor', genre='Fantasy'),
    _book('Words of Radiance', 'Brandon Sanderson', 'The Stormlight Archive', '2',
          narrator='Michael Kramer', year='2014'),
    _book('The Final Empire', 'Brandon Sanderson', 'Mistborn', '1',
          narrator='Michael Kramer', year='2006'),
    _book('Dungeon Crawler Carl', 'Matt Dinniman', 'Dungeon Crawler Carl', '1',
          narrator='Jeff Hays', year='2021'),
    _book("Ender's Game", 'Orson Scott Card', year='1985', narrator='Stefan Rudnicki'),
]


@pytest.fixture
def tree(qapp):
    from booktree import BookTreeWidget
    t = BookTreeWidget()
    t.populate(LIBRARY)
    return t


def _visible_titles(tree):
    """Titles of book nodes currently shown (not hidden, ancestors not hidden)."""
    out = []
    root = tree.invisibleRootItem()
    def walk(node):
        for i in range(node.childCount()):
            c = node.child(i)
            if c.isHidden():
                continue
            d = c.data(0, Qt.ItemDataRole.UserRole)
            if d and d[0] == tree.NODE_BOOK:
                b = tree._book_by_id(d[1])
                if b: out.append(b.title)
            else:
                walk(c)
    walk(root)
    return set(out)


def test_empty_filter_shows_all(tree):
    tree.apply_filter('')
    assert _visible_titles(tree) == {b.title for b in LIBRARY}


def test_filter_by_title(tree):
    tree.apply_filter('kings')
    assert _visible_titles(tree) == {'The Way of Kings'}


def test_filter_by_author_shows_all_their_books(tree):
    tree.apply_filter('sanderson')
    assert _visible_titles(tree) == {
        'The Way of Kings', 'Words of Radiance', 'The Final Empire'}


def test_filter_by_series(tree):
    tree.apply_filter('stormlight')
    assert _visible_titles(tree) == {'The Way of Kings', 'Words of Radiance'}


def test_filter_by_narrator(tree):
    tree.apply_filter('jeff hays')
    assert _visible_titles(tree) == {'Dungeon Crawler Carl'}


def test_filter_by_year(tree):
    tree.apply_filter('1985')
    assert _visible_titles(tree) == {"Ender's Game"}


def test_filter_by_publisher_and_genre(tree):
    tree.apply_filter('tor')
    assert 'The Way of Kings' in _visible_titles(tree)
    tree.apply_filter('fantasy')
    assert _visible_titles(tree) == {'The Way of Kings'}


def test_multi_term_is_and(tree):
    # both terms must match somewhere in the same book
    tree.apply_filter('sanderson mistborn')
    assert _visible_titles(tree) == {'The Final Empire'}
    tree.apply_filter('kramer 2014')
    assert _visible_titles(tree) == {'Words of Radiance'}


def test_no_match_hides_everything(tree):
    tree.apply_filter('nonexistent zzz')
    assert _visible_titles(tree) == set()


def test_filter_case_insensitive(tree):
    tree.apply_filter('SANDERSON')
    assert len(_visible_titles(tree)) == 3
