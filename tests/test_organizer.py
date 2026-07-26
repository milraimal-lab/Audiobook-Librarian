"""Organizer logic: sanitizing, numbering, folder layout, and the
never-overwrite move rules."""
from pathlib import Path

import organizer as org


# ── sanitize ─────────────────────────────────────────────────────

def test_sanitize_strips_forbidden_chars():
    assert org.sanitize('A<b>:c"d/e\\f|g?h*i') == 'Abcdefghi'

def test_sanitize_trims_dots_and_spaces():
    assert org.sanitize('  Book Title.  ') == 'Book Title'

def test_sanitize_empty_falls_back():
    assert org.sanitize('***') == 'Unknown'


# ── pad_num (incl. the 18.5 regression) ──────────────────────────

def test_pad_num_integers():
    assert org.pad_num('3') == '03'
    assert org.pad_num('18') == '18'
    assert org.pad_num('105') == '105'

def test_pad_num_keeps_fractions():
    assert org.pad_num('18.5') == '18.5'
    assert org.pad_num('3.5') == '03.5'
    assert org.pad_num('7.25') == '07.25'

def test_pad_num_drops_zero_decimal():
    assert org.pad_num('8.0') == '08'
    assert org.pad_num('12.00') == '12'
    assert org.pad_num('3.0') == '03'

def test_pad_num_non_numeric_passthrough():
    assert org.pad_num('abc') == 'abc'
    assert org.pad_num('') == ''


# ── build_file_name ──────────────────────────────────────────────

def test_single_file_gets_no_number():
    assert org.build_file_name('Title', '1', '1') == 'Title'

def test_small_sets_get_bare_digits():
    assert org.build_file_name('Title', '3', '5') == 'Title 3'

def test_large_sets_get_zero_padding():
    assert org.build_file_name('Title', '3', '12') == 'Title 03'
    assert org.build_file_name('Title', '3', '120') == 'Title 003'


# ── build_folder_path ────────────────────────────────────────────

def test_folder_with_series():
    p = org.build_folder_path('/lib', 'Auth', 'Series', '4', 'Title')
    assert p == Path('/lib') / 'Auth' / 'Series' / '04 Title'

def test_folder_fractional_series_number():
    p = org.build_folder_path('/lib', 'Auth', 'Series', '18.5', 'Title')
    assert p.name == '18.5 Title'

def test_folder_without_series_skips_level():
    p = org.build_folder_path('/lib', 'Auth', '', '', 'Title')
    assert p == Path('/lib') / 'Auth' / 'Title'


# ── apply_moves: the never-overwrite contract ────────────────────

def _mk(tmp_path, name, content=b'x'):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p

def test_apply_moves_moves(tmp_path):
    src = _mk(tmp_path, 'a/f.mp3')
    dst = tmp_path / 'b' / 'f.mp3'
    done, errors, performed, skipped, renamed = org.apply_moves([(src, dst)])
    assert not errors and not skipped and not renamed
    assert performed == [(src, dst)]
    assert dst.exists() and not src.exists()

def test_apply_moves_copy_keeps_original(tmp_path):
    src = _mk(tmp_path, 'a/f.mp3')
    dst = tmp_path / 'b' / 'f.mp3'
    org.apply_moves([(src, dst)], copy=True)
    assert dst.exists() and src.exists()

def test_collision_skip_never_touches_destination(tmp_path):
    src = _mk(tmp_path, 'a/f.mp3', b'incoming')
    dst = _mk(tmp_path, 'b/f.mp3', b'precious')
    done, errors, performed, skipped, renamed = org.apply_moves(
        [(src, dst)], skip_existing=True)
    assert skipped == [(src, dst)] and not performed
    assert src.exists()                          # incoming stayed put
    assert dst.read_bytes() == b'precious'       # existing untouched

def test_collision_autorename_never_overwrites(tmp_path):
    src = _mk(tmp_path, 'a/f.mp3', b'incoming')
    dst = _mk(tmp_path, 'b/f.mp3', b'precious')
    done, errors, performed, skipped, renamed = org.apply_moves(
        [(src, dst)], skip_existing=False)
    assert dst.read_bytes() == b'precious'       # still untouched
    (moved_src, final) = performed[0]
    assert final.name == 'f (2).mp3'
    assert final.read_bytes() == b'incoming'
    assert renamed == [(src, final)]

def test_autorename_walks_past_taken_names(tmp_path):
    src = _mk(tmp_path, 'a/f.mp3', b'new')
    _mk(tmp_path, 'b/f.mp3')
    _mk(tmp_path, 'b/f (2).mp3')
    _, _, performed, _, _ = org.apply_moves([(src, dst := tmp_path / 'b' / 'f.mp3')],
                                            skip_existing=False)
    assert performed[0][1].name == 'f (3).mp3'

def test_same_src_dst_is_noop(tmp_path):
    src = _mk(tmp_path, 'a/f.mp3')
    done, errors, performed, skipped, renamed = org.apply_moves([(src, src)])
    assert done == 1 and not performed and src.exists()
