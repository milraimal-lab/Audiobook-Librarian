"""Pure helpers: title parsing, filename sanitizing, ffmetadata escaping,
human-readable size/duration formatting."""
from util import (parse_audiobook_title, parse_series_group, _sanitize, _ffesc,
                  fmt_size, fmt_duration)


def test_parse_paren_series_hash():
    out = parse_audiobook_title('The Inquisition (Summoner, #2)')
    assert out == {'title': 'The Inquisition', 'series': 'Summoner',
                   'series_num': '2'}

def test_parse_paren_series_book_word():
    out = parse_audiobook_title('The Novice (Summoner, Book 1)')
    assert out['series'] == 'Summoner' and out['series_num'] == '1'

def test_parse_prefix_series_dash_title():
    out = parse_audiobook_title('Summoner #2 - The Inquisition')
    assert out == {'series': 'Summoner', 'series_num': '2',
                   'title': 'The Inquisition'}

def test_parse_fractional_number():
    out = parse_audiobook_title('Interlude (Dungeon Crawler Carl, #18.5)')
    assert out['series_num'] == '18.5'

def test_parse_plain_title_untouched():
    assert parse_audiobook_title('Just A Title') == {'title': 'Just A Title'}

def test_parse_bracket_series_number():
    out = parse_audiobook_title('The Last Gunfighter [18] Killing Ground')
    assert out == {'series': 'The Last Gunfighter', 'series_num': '18',
                   'title': 'Killing Ground'}

def test_parse_bracket_year_not_series():
    # A 4-digit parenthetical is a year, not a series index — leave it alone.
    assert parse_audiobook_title('Some Book (2020)') == {'title': 'Some Book (2020)'}


def test_group_infers_series_from_shared_prefix():
    names = [
        'The Last Gunfighter [18] Killing Ground',
        'The Last Gunfighter [05] Showdown',
        'The Last Gunfighter 11',
    ]
    out = parse_series_group(names)
    assert out[0] == {'series': 'The Last Gunfighter', 'series_num': '18',
                      'title': 'Killing Ground'}
    assert out[1]['series_num'] == '5'                      # leading zero stripped
    assert out[2] == {'series': 'The Last Gunfighter', 'series_num': '11',
                      'title': 'Book 11'}                   # no subtitle -> "Book N"

def test_group_clusters_two_series():
    names = ['Wheel of Time #1 The Eye of the World',
             'Wheel of Time #2 The Great Hunt',
             'Discworld Book 3 Equal Rites',
             'Discworld Book 4 Mort']
    out = parse_series_group(names)
    assert {o['series'] for o in out} == {'Wheel of Time', 'Discworld'}
    assert out[2] == {'series': 'Discworld', 'series_num': '3', 'title': 'Equal Rites'}

def test_group_leaves_standalones_alone():
    names = ['1984', 'Catch 22', 'The Hobbit']
    assert parse_series_group(names) == [{'title': '1984'},
                                         {'title': 'Catch 22'},
                                         {'title': 'The Hobbit'}]


def test_sanitize_replaces_forbidden_with_underscore():
    assert _sanitize('a:b*c') == 'a_b_c'

def test_sanitize_empty_is_unknown():
    assert _sanitize('   ') == 'Unknown'


def test_ffesc_escapes_metadata_specials():
    assert _ffesc('a=b;c#d\\e') == 'a\\=b\\;c\\#d\\\\e'

def test_ffesc_none_is_empty():
    assert _ffesc(None) == ''


def test_fmt_size_scales_units():
    assert fmt_size(512) == '512 B'
    assert fmt_size(2048) == '2 KB'
    assert fmt_size(5 * 1024 ** 2) == '5 MB'
    assert fmt_size(int(1.5 * 1024 ** 3)) == '1.50 GB'

def test_fmt_size_handles_zero_and_none():
    assert fmt_size(0) == '0 B'
    assert fmt_size(None) == '0 B'


def test_fmt_duration_hours_and_minutes():
    assert fmt_duration(3600) == '1h 00m'
    assert fmt_duration(3600 * 24 + 600) == '24h 10m'

def test_fmt_duration_minutes_only():
    assert fmt_duration(47 * 60) == '47m'

def test_fmt_duration_unknown():
    assert fmt_duration(0) == '--'
    assert fmt_duration(None) == '--'
