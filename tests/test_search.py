"""Search Metadata dialog: 'By series' seeds the query from series+author,
and results are sortable without breaking cover/pick association."""
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import scanner as sc

pytest.importorskip('PyQt6.QtWidgets')
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # Never let dialog construction fire a real search, and isolate settings.
    import dialogs
    monkeypatch.setattr(dialogs.OpenLibraryDialog, '_search', lambda self: None)
    monkeypatch.setattr(dialogs, '_save_settings', lambda d: None)


def _mkbook():
    b = sc.Book()
    b.title = 'Wrong Ripped Title'
    b.author = 'Matt Dinniman'
    b.series = 'Dungeon Crawler Carl'
    return b


def test_seed_title_author_by_default(qapp, monkeypatch):
    import dialogs
    monkeypatch.setattr(dialogs, '_load_settings', lambda: {})
    dlg = dialogs.OpenLibraryDialog(_mkbook())
    assert dlg.q_edit.text() == 'Wrong Ripped Title Matt Dinniman'
    assert not dlg._by_series


def test_seed_series_author_when_pref_on(qapp, monkeypatch):
    import dialogs
    monkeypatch.setattr(dialogs, '_load_settings', lambda: {'search_by_series': True})
    dlg = dialogs.OpenLibraryDialog(_mkbook())
    assert dlg._by_series
    assert dlg.q_edit.text() == 'Dungeon Crawler Carl Matt Dinniman'


def test_seed_falls_back_to_title_when_no_series(qapp, monkeypatch):
    import dialogs
    monkeypatch.setattr(dialogs, '_load_settings', lambda: {'search_by_series': True})
    b = _mkbook(); b.series = ''
    dlg = dialogs.OpenLibraryDialog(b)
    # no series to search by → falls back to title+author
    assert dlg.q_edit.text() == 'Wrong Ripped Title Matt Dinniman'


def test_results_table_sorting_enabled(qapp, monkeypatch):
    import dialogs
    monkeypatch.setattr(dialogs, '_load_settings', lambda: {})
    dlg = dialogs.OpenLibraryDialog(_mkbook())
    assert dlg.tbl.isSortingEnabled()


def test_sorting_keeps_cover_mapping(qapp, monkeypatch):
    """After re-sorting, a row still resolves to its original result index, so
    the cover/pick association survives (the reason picks store the item and
    covers key by result index)."""
    import dialogs
    from PyQt6.QtWidgets import QTableWidgetItem
    from PyQt6.QtCore import Qt
    monkeypatch.setattr(dialogs, '_load_settings', lambda: {})
    dlg = dialogs.OpenLibraryDialog(_mkbook())

    dlg._results = [{'title': 'Zeta'}, {'title': 'Alpha'}]
    dlg._filling = True
    dlg.tbl.setSortingEnabled(False)
    dlg.tbl.setRowCount(0)
    for ri, r in enumerate(dlg._results):
        row = dlg.tbl.rowCount(); dlg.tbl.insertRow(row)
        use = QTableWidgetItem(''); use.setData(Qt.ItemDataRole.UserRole, ri)
        dlg.tbl.setItem(row, 0, use)
        dlg.tbl.setItem(row, 1, QTableWidgetItem(r['title']))   # Title column = 1
    dlg._filling = False
    dlg.tbl.setSortingEnabled(True)
    dlg._covers[0] = b'zeta-cover'                              # cover for 'Zeta'

    # sort by Title ascending → Alpha first, Zeta second
    dlg.tbl.sortItems(1, Qt.SortOrder.AscendingOrder)
    zeta_row = next(r for r in range(dlg.tbl.rowCount())
                    if dlg.tbl.item(r, 1).text() == 'Zeta')
    assert zeta_row == 1                                        # moved from row 0
    assert dlg._result_index(zeta_row) == 0                    # still maps to result 0
    dlg.tbl.selectRow(zeta_row)
    assert dlg._covers.get(dlg._current_result_index()) == b'zeta-cover'
