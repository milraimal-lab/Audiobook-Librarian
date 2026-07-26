"""Tagger integration tests against real files generated with the bundled
ffmpeg. Skipped entirely when ffmpeg isn't available."""
import shutil
import subprocess

import pytest
from mutagen.id3 import ID3, TPE1, TPE2, TCOM, TXXX

import tagger as tg
from util import find_ffmpeg

FFMPEG = find_ffmpeg()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not found")


def _gen(path, seconds='0.3', freq='440'):
    """Write a tiny real audio file (mp3, m4b, or flac) at *path*."""
    codec = ['-c:a', 'aac', '-f', 'ipod'] if path.suffix == '.m4b' else []
    subprocess.run(
        [FFMPEG, '-y', '-f', 'lavfi', '-i', f'sine=frequency={freq}:sample_rate=22050',
         '-t', seconds, *codec, str(path)],
        check=True, capture_output=True)
    return path


@pytest.fixture(scope='session')
def base_mp3(tmp_path_factory):
    return _gen(tmp_path_factory.mktemp('audio') / 'base.mp3')

@pytest.fixture(scope='session')
def base_m4b(tmp_path_factory):
    return _gen(tmp_path_factory.mktemp('audio') / 'base.m4b')

@pytest.fixture
def mp3(base_mp3, tmp_path):
    return shutil.copy2(base_mp3, tmp_path / 'test.mp3')

@pytest.fixture
def m4b(base_m4b, tmp_path):
    return shutil.copy2(base_m4b, tmp_path / 'test.m4b')

@pytest.fixture(scope='session')
def base_flac(tmp_path_factory):
    return _gen(tmp_path_factory.mktemp('audio') / 'base.flac')

@pytest.fixture
def flac(base_flac, tmp_path):
    return shutil.copy2(base_flac, tmp_path / 'test.flac')


# ── round trips ──────────────────────────────────────────────────

FIELDS = dict(title='My Book', album='My Book', author='Jane Doe',
              artist='Jane Doe', narrator='Bob Reader', composer='Bob Reader',
              series='Great Series', series_num='18.5', year='2024',
              publisher='Pub House', genre='Fantasy',
              description='A very good book.')

def test_mp3_round_trip(mp3):
    tg.write_tags(mp3, dict(FIELDS))
    got = tg.read_tags(mp3)
    for key in ('title', 'author', 'narrator', 'series', 'series_num',
                'year', 'publisher', 'genre'):
        assert got[key] == FIELDS[key], key

def test_m4b_round_trip(m4b):
    tg.write_tags(m4b, dict(FIELDS))
    got = tg.read_tags(m4b)
    for key in ('title', 'author', 'narrator', 'series', 'series_num',
                'year', 'publisher'):
        assert got[key] == FIELDS[key], key

def test_flac_round_trip(flac):
    tg.write_tags(flac, dict(FIELDS))
    got = tg.read_tags(flac)
    for key in ('title', 'author', 'narrator', 'series', 'series_num',
                'year', 'publisher', 'genre'):
        assert got[key] == FIELDS[key], key

def test_flac_delete_field(flac):
    tg.write_tags(flac, dict(FIELDS))
    assert tg.delete_field(flac, 'series_num')
    assert tg.read_tags(flac).get('series_num', '') == ''

def test_flac_audio_hash_ignores_tags(flac, tmp_path):
    twin = shutil.copy2(flac, tmp_path / 'twin.flac')
    tg.write_tags(twin, {'title': 'Different', 'author': 'Different',
                         'series_num': '99'})
    assert tg.audio_content_md5(flac) == tg.audio_content_md5(twin)

def test_partial_write_touches_only_that_field(mp3):
    tg.write_tags(mp3, dict(FIELDS))
    tg.write_tags(mp3, {'series_num': '7'})
    got = tg.read_tags(mp3)
    assert got['series_num'] == '7'
    assert got['title'] == FIELDS['title']      # untouched

@pytest.mark.parametrize('ext', ['.mp3', '.m4b', '.flac'])
def test_series_num_zero_decimal_normalised(ext, base_mp3, base_m4b, base_flac, tmp_path):
    # A file tagged '8.0' (the Dresden Files case) must read back as '8',
    # while a real fraction '8.5' is preserved — across formats.
    src = {'.mp3': base_mp3, '.m4b': base_m4b, '.flac': base_flac}[ext]
    f = shutil.copy2(src, tmp_path / f'sn{ext}')
    tg.write_tags(f, {'title': 'X', 'author': 'Y', 'series': 'S', 'series_num': '8.0'})
    assert tg.read_tags(f)['series_num'] == '8'
    tg.write_tags(f, {'series_num': '8.5'})
    assert tg.read_tags(f)['series_num'] == '8.5'


# ── narrator rules (author-as-narrator regression) ───────────────

def test_album_artist_equal_to_artist_is_not_narrator(mp3):
    tags = ID3()
    tags.add(TPE1(encoding=3, text='The Author'))
    tags.add(TPE2(encoding=3, text='The Author'))   # generic tagger dupe
    tags.save(str(mp3))
    assert tg.read_tags(mp3)['narrator'] == ''

def test_distinct_album_artist_is_narrator(mp3):
    tags = ID3()
    tags.add(TPE1(encoding=3, text='The Author'))
    tags.add(TPE2(encoding=3, text='The Narrator'))
    tags.save(str(mp3))
    assert tg.read_tags(mp3)['narrator'] == 'The Narrator'

def test_composer_frame_wins_as_narrator(mp3):
    tags = ID3()
    tags.add(TPE1(encoding=3, text='The Author'))
    tags.add(TCOM(encoding=3, text='The Narrator'))
    tags.save(str(mp3))
    assert tg.read_tags(mp3)['narrator'] == 'The Narrator'


# ── TXXX must never override standard frames (zIlona regression) ─

def test_txxx_author_does_not_override_tpe1(mp3):
    tags = ID3()
    tags.add(TPE1(encoding=3, text='Ilona'))
    tags.add(TXXX(encoding=3, desc='author', text='zIlona'))
    tags.save(str(mp3))
    assert tg.read_tags(mp3)['author'] == 'Ilona'


# ── field deletion (clear via empty Apply) ───────────────────────

def test_delete_field_removes_all_variants(mp3):
    tg.write_tags(mp3, dict(FIELDS))
    assert tg.read_tags(mp3)['series_num'] == '18.5'
    assert tg.delete_field(mp3, 'series_num')
    assert tg.read_tags(mp3).get('series_num', '') == ''


# ── standard-tag registry drives both Extras panel and Purge ─────

def test_is_standard_tag():
    assert tg.is_standard_tag('TIT2')
    assert tg.is_standard_tag('TXXX:SERIES_INDEX')
    assert tg.is_standard_tag('COMM::eng')
    assert not tg.is_standard_tag('TXXX:RANDOM_JUNK')
    assert not tg.is_standard_tag('WXXX:ad-url')


# ── tag-agnostic audio hashing ───────────────────────────────────

def test_audio_hash_ignores_tag_differences(mp3, tmp_path):
    twin = shutil.copy2(mp3, tmp_path / 'twin.mp3')
    tg.write_tags(twin, {'title': 'Completely Different',
                         'author': 'Someone Else', 'series_num': '99'})
    import hashlib
    assert (hashlib.md5(mp3.read_bytes()).hexdigest()
            != hashlib.md5(twin.read_bytes()).hexdigest())   # bytes differ
    assert tg.audio_content_md5(mp3) == tg.audio_content_md5(twin)

def test_audio_hash_differs_for_different_audio(mp3, tmp_path):
    other = _gen(tmp_path / 'other.mp3', freq='880')
    assert tg.audio_content_md5(mp3) != tg.audio_content_md5(other)

def test_m4b_audio_hash_ignores_tags(m4b, tmp_path):
    twin = shutil.copy2(m4b, tmp_path / 'twin.m4b')
    tg.write_tags(twin, {'title': 'Different', 'author': 'Different'})
    assert tg.audio_content_md5(m4b) == tg.audio_content_md5(twin)
