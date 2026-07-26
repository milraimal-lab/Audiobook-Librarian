"""
tagger.py – Read and write audio tags across MP3, M4B/M4A, FLAC, OGG, Opus.

All formats are normalised into/from a common dict with these keys:
  title, album, author, artist, narrator, composer, series, series_num,
  grouping, year, date, publisher, genre, comment, description,
  track, track_total, disc, cover_art (bytes | None), duration (float)
"""

import base64
from pathlib import Path
from typing import Optional

from util import norm_series_num

# ──────────────────────────────────────────────────────────────────
#  Standard tags — single source of truth for the Extras panel and
#  purge_extra_tags. Everything NOT matched here is an "extra".
# ──────────────────────────────────────────────────────────────────

STANDARD_KEYS = {
    # ID3
    'TIT2', 'TIT1', 'TIT3', 'TPE1', 'TPE2', 'TALB', 'TDRC', 'TRCK', 'TPOS',
    'TCON', 'TPUB', 'TCOM', 'TCOP', 'TLAN', 'APIC', 'WOAS', 'TMOO',
    'TXXX:SERIES_INDEX', 'TXXX:series_index', 'TXXX:series_num', 'TXXX:SERIES_NUM',
    'TXXX:ASIN', 'TXXX:ISBN', 'TXXX:RATING',
    # MP4
    '\xa9nam', '\xa9ART', '\xa9alb', 'aART', '\xa9day', '\xa9gen', '\xa9cmt',
    '\xa9wrt', '\xa9grp', '\xa9lyr', 'cprt', 'covr', 'desc', 'trkn', 'disk',
    '----:com.apple.iTunes:SERIES', '----:com.apple.iTunes:SERIES-PART',
    '----:com.apple.iTunes:SERIES_NUM', '----:com.apple.iTunes:PUBLISHER',
    '----:com.apple.iTunes:SUBTITLE', '----:com.apple.iTunes:LANGUAGE',
    '----:com.apple.iTunes:ASIN', '----:com.apple.iTunes:ISBN',
    '----:com.apple.iTunes:MOOD', '----:com.apple.iTunes:RATING',
    '----:com.apple.iTunes:WEBSITE', '----:com.apple.iTunes:NARRATOR',
    # Vorbis / FLAC
    'title', 'artist', 'album', 'albumartist', 'performer', 'composer',
    'date', 'year', 'genre', 'organization', 'publisher', 'comment',
    'description', 'tracknumber', 'tracktotal', 'discnumber', 'disctotal',
    'series', 'series-part', 'series_num', 'grouping', 'subtitle',
    'copyright', 'language', 'asin', 'isbn', 'mood', 'rating', 'website',
    'narrator', 'metadata_block_picture',
}

# Keys whose every variant (before the ':') is standard, e.g. COMM::eng
STANDARD_PREFIXES = {'COMM', 'APIC', 'USLT'}


def is_standard_tag(key: str) -> bool:
    base = key.split(':')[0] if ':' in key else key
    return key in STANDARD_KEYS or base in STANDARD_KEYS or base in STANDARD_PREFIXES

import mutagen
import mutagen.mp4
from mutagen.mp3 import MP3
from mutagen.id3 import (
    ID3, ID3NoHeaderError,
    TIT2, TIT1, TIT3, TPE1, TPE2, TALB, TDRC, TRCK, TPOS,
    TCOM, TCOP, TLAN, COMM, TCON, TPUB, TXXX, APIC,
)
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

# ──────────────────────────────────────────────────────────────────
#  Patch: malformed chapter atoms must not kill tag reading.
#
#  Many m4b files in the wild have truncated or non-UTF-8 'chpl'
#  chapter data. Mutagen raises ("unpack requires a buffer of 8
#  bytes", "chapter N title: 'utf-8' codec can't decode…") while
#  LOADING the file, so perfectly good tags become unreadable.
#  We don't use MP4 chapter data — swallow those errors.
# ──────────────────────────────────────────────────────────────────

_orig_chapters_load = mutagen.mp4.MP4Chapters.load

def _tolerant_chapters_load(self, atoms, fileobj):
    try:
        return _orig_chapters_load(self, atoms, fileobj)
    except Exception:
        self._chapters = []

mutagen.mp4.MP4Chapters.load = _tolerant_chapters_load
from mutagen.flac import FLAC, Picture
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus


# ──────────────────────────────────────────────────────────────────
#  Public read
# ──────────────────────────────────────────────────────────────────

def read_tags(path: Path) -> dict:
    """Return a normalised tag dict for any supported audio file."""
    ext = path.suffix.lower()
    try:
        if ext == '.mp3':
            result = _read_mp3(path)
        elif ext in ('.m4b', '.m4a', '.aac', '.mp4', '.alac'):
            result = _read_m4b(path)
        elif ext == '.flac':
            result = _read_flac(path)
        elif ext == '.ogg':
            result = _read_ogg(path)
        elif ext == '.opus':
            result = _read_opus(path)
        else:
            # Generic fallback (wav, wma, aiff …)
            audio = mutagen.File(path)
            result = {'duration': getattr(audio.info, 'length', 0.0)} if audio else {}
    except Exception as exc:
        print(f"[tagger] read error {path.name}: {exc}")
        return {'_error': str(exc)}
    # Whole-number series indices shouldn't carry a '.0' ('08.0' → '08')
    if result.get('series_num'):
        result['series_num'] = norm_series_num(result['series_num'])
    return result


def audio_content_md5(path: Path) -> Optional[str]:
    """MD5 of just the AUDIO stream, skipping tag containers where the format
    allows — so two copies that differ only in tags/covers hash identically.

    MP3: skips the ID3v2 header and ID3v1/APEv2 tails.
    MP4/M4B: hashes only the 'mdat' atom payloads.
    FLAC: skips the metadata blocks.
    Other formats: whole file.
    """
    import hashlib
    ext = path.suffix.lower()
    h = hashlib.md5()
    chunk = 1 << 20
    try:
        with open(path, 'rb') as f:
            if ext == '.mp3':
                start = 0
                head = f.read(10)
                if head[:3] == b'ID3' and len(head) == 10:
                    size = ((head[6] & 0x7f) << 21) | ((head[7] & 0x7f) << 14) \
                         | ((head[8] & 0x7f) << 7) | (head[9] & 0x7f)
                    start = 10 + size
                    if head[5] & 0x10:      # footer present flag
                        start += 10
                f.seek(0, 2); end = f.tell()
                if end - start > 128:       # ID3v1 tail
                    f.seek(end - 128)
                    if f.read(3) == b'TAG': end -= 128
                if end - start > 32:        # APEv2 tail
                    f.seek(end - 32)
                    foot = f.read(32)
                    if foot[:8] == b'APETAGEX':
                        tag_size = int.from_bytes(foot[12:16], 'little')
                        flags    = int.from_bytes(foot[20:24], 'little')
                        end -= tag_size
                        if flags & (1 << 31):   # tag has a header block too
                            end -= 32
                if end <= start:
                    return None
                f.seek(start)
                remaining = end - start
                while remaining > 0:
                    data = f.read(min(chunk, remaining))
                    if not data: break
                    h.update(data); remaining -= len(data)
                return h.hexdigest()

            if ext in ('.m4b', '.m4a', '.mp4', '.aac', '.alac'):
                f.seek(0, 2); fsize = f.tell()
                pos = 0; found = False
                while pos < fsize - 8:
                    f.seek(pos)
                    hdr = f.read(8)
                    if len(hdr) < 8: break
                    size = int.from_bytes(hdr[:4], 'big'); name = hdr[4:8]
                    hsz = 8
                    if size == 1:
                        size = int.from_bytes(f.read(8), 'big'); hsz = 16
                    elif size == 0:
                        size = fsize - pos
                    if size < hsz: break
                    if name == b'mdat':
                        found = True
                        remaining = size - hsz
                        while remaining > 0:
                            data = f.read(min(chunk, remaining))
                            if not data: break
                            h.update(data); remaining -= len(data)
                    pos += size
                if found:
                    return h.hexdigest()
                f.seek(0)   # no mdat found — fall through to whole file

            elif ext == '.flac':
                if f.read(4) == b'fLaC':
                    last = False
                    while not last:
                        bh = f.read(4)
                        if len(bh) < 4: return None
                        last = bool(bh[0] & 0x80)
                        f.seek(int.from_bytes(bh[1:4], 'big'), 1)
                    for data in iter(lambda: f.read(chunk), b''):
                        h.update(data)
                    return h.hexdigest()
                f.seek(0)

            for data in iter(lambda: f.read(chunk), b''):
                h.update(data)
        return h.hexdigest()
    except OSError:
        return None


def read_all_tags_raw(path: Path) -> dict:
    """Return every tag present in the file (key: raw value string)."""
    try:
        audio = mutagen.File(path, easy=False)
        if audio and audio.tags:
            return {str(k): str(v) for k, v in audio.tags.items()}
    except Exception:
        pass
    return {}


# ──────────────────────────────────────────────────────────────────
#  Public write
# ──────────────────────────────────────────────────────────────────

def write_tags(path: Path, fields: dict) -> None:
    """Write normalised *fields* dict to the file's tags."""
    ext = path.suffix.lower()
    # Persist series indices clean: '8.0' → '8' (never overwrite the caller's
    # dict — take a shallow copy only when there's something to normalise)
    if fields.get('series_num'):
        cleaned = norm_series_num(fields['series_num'])
        if cleaned != fields['series_num']:
            fields = {**fields, 'series_num': cleaned}
    try:
        if ext == '.mp3':
            _write_mp3(path, fields)
        elif ext in ('.m4b', '.m4a', '.aac', '.mp4', '.alac'):
            _write_m4b(path, fields)
        elif ext == '.flac':
            _write_flac(path, fields)
        elif ext == '.ogg':
            _write_ogg(path, fields)
        elif ext == '.opus':
            _write_opus(path, fields)
    except Exception as exc:
        print(f"[tagger] write error {path.name}: {exc}")
        raise


# Field → the tag keys to remove per format when clearing it
_DELETE_KEYS = {
    'title':       {'mp3': ['TIT2', 'TALB'], 'mp4': ['\xa9nam', '\xa9alb'],
                    'vorbis': ['title', 'album']},
    'author':      {'mp3': ['TPE1'], 'mp4': ['\xa9ART'], 'vorbis': ['artist']},
    'narrator':    {'mp3': ['TPE2', 'TCOM'],
                    'mp4': ['aART', '\xa9wrt', '----:com.apple.iTunes:NARRATOR'],
                    'vorbis': ['performer', 'narrator', 'composer']},
    'series':      {'mp3': ['TIT1'], 'mp4': ['----:com.apple.iTunes:SERIES'],
                    'vorbis': ['series']},
    'series_num':  {'mp3': ['TXXX:SERIES_INDEX', 'TXXX:series_index',
                            'TXXX:series_num', 'TXXX:SERIES_NUM'],
                    'mp4': ['----:com.apple.iTunes:SERIES-PART',
                            '----:com.apple.iTunes:SERIES_NUM'],
                    'vorbis': ['series-part', 'series_num']},
    'year':        {'mp3': ['TDRC'], 'mp4': ['\xa9day'], 'vorbis': ['date', 'year']},
    'publisher':   {'mp3': ['TPUB'], 'mp4': ['----:com.apple.iTunes:PUBLISHER'],
                    'vorbis': ['organization', 'publisher']},
    'genre':       {'mp3': ['TCON'], 'mp4': ['\xa9gen'], 'vorbis': ['genre']},
    'description': {'mp3': ['COMM'], 'mp4': ['desc', '\xa9cmt'],
                    'vorbis': ['description', 'comment']},
}


def delete_field(path: Path, field_key: str) -> bool:
    """Remove a normalised field's tags from the file entirely."""
    ext = path.suffix.lower()
    spec = _DELETE_KEYS.get(field_key)
    if not spec:
        return False
    try:
        if ext == '.mp3':
            try: tags = ID3(path)
            except ID3NoHeaderError: return True
            for k in spec['mp3']:
                tags.delall(k)
            tags.save(str(path), v2_version=3)
            return True
        if ext in ('.m4b', '.m4a', '.aac', '.mp4', '.alac'):
            audio = MP4(path)
            if audio.tags is None: return True
            for k in spec['mp4']:
                if k in audio.tags: del audio.tags[k]
            audio.save()
            return True
        if ext in ('.flac', '.ogg', '.opus'):
            audio = {'.flac': FLAC, '.ogg': OggVorbis, '.opus': OggOpus}[ext](path)
            if audio.tags is None: return True
            for k in spec['vorbis']:
                if k in audio: del audio[k]
            audio.save()
            return True
    except Exception as exc:
        print(f"[tagger] delete error {path.name} {field_key}: {exc}")
    return False


def add_raw_tag(path: Path, key: str, value: str) -> bool:
    """
    Add a brand-new tag to a file (or overwrite if it already exists).
    Returns True on success.

    Supported key formats:
      • MP3 / ID3:  standard frame names (TIT3, TCOM, …) or TXXX:NAME custom frames
      • MP4 / M4B:  standard atoms (©nam, cprt, …) or ----:com.apple.iTunes:NAME freeform
      • Vorbis (FLAC/OGG/Opus): any simple key (will be lowercased)
    """
    ext = path.suffix.lower()
    try:
        if ext == '.mp3':
            try: tags = ID3(path)
            except ID3NoHeaderError: tags = ID3()

            if key.startswith('TXXX:'):
                desc = key[5:]
                tags.setall(key, [TXXX(encoding=3, desc=desc, text=str(value))])
            elif key.startswith('COMM'):
                tags.setall('COMM',
                            [COMM(encoding=3, lang='eng', desc='', text=str(value))])
            elif key.startswith('APIC'):
                return False  # use cover_art write path for images
            else:
                from mutagen.id3 import Frames
                if key in Frames:
                    cls = Frames[key]
                    try:
                        tags.setall(key, [cls(encoding=3, text=str(value))])
                    except TypeError:
                        try: tags.setall(key, [cls(text=str(value))])
                        except Exception: return False
                else:
                    return False
            tags.save(str(path), v2_version=3)
            return True

        elif ext in ('.m4b', '.m4a', '.aac', '.mp4', '.alac'):
            audio = MP4(path)
            if audio.tags is None: audio.add_tags()
            t = audio.tags
            if key.startswith('----:'):
                t[key] = [MP4FreeForm(str(value).encode('utf-8'),
                                      dataformat=MP4FreeForm.FORMAT_UTF8)]
            else:
                t[key] = [str(value)]
            audio.save()
            return True

        elif ext == '.flac':
            audio = FLAC(path); audio[key.lower()] = [str(value)]; audio.save(); return True
        elif ext == '.ogg':
            audio = OggVorbis(path); audio[key.lower()] = [str(value)]; audio.save(); return True
        elif ext == '.opus':
            audio = OggOpus(path); audio[key.lower()] = [str(value)]; audio.save(); return True
    except Exception as exc:
        print(f"[tagger] add_raw_tag {path.name} {key}: {exc}")
    return False


def update_raw_tag(path: Path, key: str, value: str) -> bool:
    """
    Update an existing tag in-place using its raw key (as shown by
    read_all_tags_raw). Returns True on success.

    Designed for the 'extra tags' editor — only modifies tags that
    already exist on the file. Best-effort across formats.
    """
    try:
        audio = mutagen.File(path, easy=False)
        if not audio or audio.tags is None:
            return False
        tags = audio.tags
        if key not in tags:
            return False

        existing = tags[key]

        # ID3 frames — have a `.text` list
        if hasattr(existing, 'text'):
            try:
                existing.text = [value]
            except Exception:
                existing.text = value
        # MP4 free-form (----:com.apple.iTunes:...) — list of bytes / MP4FreeForm
        elif isinstance(existing, list) and existing and isinstance(existing[0], (bytes, bytearray, MP4FreeForm)):
            tags[key] = [MP4FreeForm(value.encode('utf-8'),
                                     dataformat=MP4FreeForm.FORMAT_UTF8)]
        # MP4 standard list of strings
        elif isinstance(existing, list):
            tags[key] = [value]
        else:
            tags[key] = value

        audio.save()
        return True
    except Exception as exc:
        print(f"[tagger] update_raw_tag {path.name} {key}: {exc}")
        return False


def purge_extra_tags(path: Path, keep_keys: Optional[set] = None) -> list:
    """
    Remove non-standard tags from file (exactly the tags the Extras panel
    shows). Returns list of tag names that were removed.
    """
    removed = []
    try:
        audio = mutagen.File(path, easy=False)
        if audio and audio.tags:
            for k in list(audio.tags.keys()):
                if keep_keys is not None:
                    base = k.split(':')[0] if ':' in k else k
                    keep = k in keep_keys or base in keep_keys
                else:
                    keep = is_standard_tag(k)
                if not keep:
                    del audio.tags[k]
                    removed.append(k)
            if removed:
                audio.save()
    except Exception as exc:
        print(f"[tagger] purge error {path.name}: {exc}")
    return removed


# ──────────────────────────────────────────────────────────────────
#  MP3
# ──────────────────────────────────────────────────────────────────

def _read_mp3(path: Path) -> dict:
    result: dict = {}
    audio = MP3(path)
    result['duration'] = audio.info.length

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return result

    def s(key):
        v = tags.get(key)
        return str(v) if v else ''

    tpe1 = s('TPE1'); tpe2 = s('TPE2')
    result['title']     = s('TIT2')
    result['author']    = tpe1
    result['artist']    = tpe1
    result['album']     = s('TALB')
    # Narrator: composer frame first (audiobook convention). Album-artist
    # only counts when it DIFFERS from the artist — identical means a
    # generic tagger just duplicated the author there.
    result['narrator']  = s('TCOM') or (tpe2 if tpe2 and tpe2 != tpe1 else '')
    result['composer']  = s('TCOM') or tpe2
    result['year']      = s('TDRC')
    result['genre']     = s('TCON')
    result['publisher'] = s('TPUB')
    result['series']    = s('TIT1')
    result['grouping']  = s('TIT1')
    result['subtitle']  = s('TIT3')
    result['copyright'] = s('TCOP')
    result['language']  = s('TLAN')
    result['disc']      = s('TPOS')

    # Comment
    comm = tags.get('COMM::eng') or tags.get('COMM')
    result['comment'] = str(comm) if comm else ''

    # Track / total
    trck = s('TRCK')
    if '/' in trck:
        n, tot = trck.split('/', 1)
        result['track'] = n.strip()
        result['track_total'] = tot.strip()
    else:
        result['track'] = trck

    # TXXX custom frames — must NEVER override the standard frames
    # (e.g. a stray TXXX:author must not beat TPE1)
    for key in tags.keys():
        if key.startswith('TXXX:'):
            name = key[5:].lower().replace(' ', '_').replace('-', '_')
            if not result.get(name):
                result[name] = str(tags[key])

    # Resolve series_num from the various TXXX names people use.
    # Priority: SERIES_INDEX (user's preferred) > series_num > series_part
    for variant in ('series_index', 'series_num', 'series_part', 'series_part'):
        if result.get(variant):
            result['series_num'] = result[variant]
            break

    # Cover art
    for key in list(tags.keys()):
        if key.startswith('APIC'):
            result['cover_art'] = tags[key].data
            break

    return result


def _write_mp3(path: Path, fields: dict) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    def set_t(cls, val, **kw):
        if val is not None and str(val).strip() != '':
            tags.setall(cls.__name__, [cls(encoding=3, text=str(val), **kw)])

    set_t(TIT2, fields.get('title'))
    set_t(TPE1, fields.get('author') or fields.get('artist'))
    set_t(TALB, fields.get('album') or fields.get('title'))
    set_t(TPE2, fields.get('narrator') or fields.get('composer'))
    set_t(TDRC, fields.get('year') or fields.get('date'))
    set_t(TCON, fields.get('genre'))
    set_t(TPUB, fields.get('publisher'))
    set_t(TIT1, fields.get('series') or fields.get('grouping'))
    set_t(TIT3, fields.get('subtitle'))
    set_t(TCOM, fields.get('composer'))
    set_t(TCOP, fields.get('copyright'))
    set_t(TLAN, fields.get('language'))
    set_t(TPOS, fields.get('disc'))

    for fk, txxx_desc in (('asin', 'ASIN'), ('isbn', 'ISBN')):
        v = fields.get(fk)
        if v:
            tags.setall(f'TXXX:{txxx_desc}',
                        [TXXX(encoding=3, desc=txxx_desc, text=str(v))])

    comment = fields.get('comment') or fields.get('description')
    if comment:
        tags.setall('COMM', [COMM(encoding=3, lang='eng', desc='', text=str(comment))])

    track = fields.get('track', '')
    total = fields.get('track_total', '')
    if track:
        trck = f"{track}/{total}" if total else str(track)
        tags.setall('TRCK', [TRCK(encoding=3, text=trck)])

    series_num = fields.get('series_num', '')
    if series_num:
        # Write as TXXX:SERIES_INDEX (user's preferred tag) plus legacy series_num
        tags.setall('TXXX:SERIES_INDEX',
                    [TXXX(encoding=3, desc='SERIES_INDEX', text=str(series_num))])
        tags.setall('TXXX:series_num',
                    [TXXX(encoding=3, desc='series_num', text=str(series_num))])

    cover = fields.get('cover_art')
    if cover:
        tags.setall('APIC', [APIC(encoding=3, mime='image/jpeg',
                                  type=3, desc='Cover', data=cover)])

    tags.save(str(path), v2_version=3)


# ──────────────────────────────────────────────────────────────────
#  M4B / M4A / AAC / MP4
# ──────────────────────────────────────────────────────────────────

def _mp4s(val) -> str:
    """Safely convert MP4 tag value to str."""
    if val is None:
        return ''
    if isinstance(val, list):
        val = val[0] if val else ''
    if isinstance(val, (bytes, bytearray)):
        return val.decode('utf-8', errors='replace')
    if hasattr(val, '__bytes__'):
        return bytes(val).decode('utf-8', errors='replace')
    return str(val)


def _read_m4b(path: Path) -> dict:
    result: dict = {}
    audio = MP4(path)
    result['duration'] = audio.info.length
    t = audio.tags or {}

    result['title']       = _mp4s(t.get('\xa9nam'))
    result['author']      = _mp4s(t.get('\xa9ART'))
    result['artist']      = result['author']
    result['album']       = _mp4s(t.get('\xa9alb'))
    _aart = _mp4s(t.get('aART'))
    _wrt  = _mp4s(t.get('\xa9wrt'))
    _nrt  = _mp4s(t.get('----:com.apple.iTunes:NARRATOR'))
    # Same rule as MP3: album-artist == artist means it's the author, not
    # the narrator
    result['narrator']    = _nrt or _wrt or (
        _aart if _aart and _aart != result['author'] else '')
    result['composer']    = _wrt
    result['year']        = _mp4s(t.get('\xa9day'))
    result['genre']       = _mp4s(t.get('\xa9gen'))
    result['comment']     = _mp4s(t.get('\xa9cmt'))
    result['description'] = _mp4s(t.get('desc')) or _mp4s(t.get('\xa9lyr'))
    result['grouping']    = _mp4s(t.get('\xa9grp'))
    result['copyright']   = _mp4s(t.get('cprt'))
    result['series']      = _mp4s(t.get('----:com.apple.iTunes:SERIES'))
    result['series_num']  = _mp4s(t.get('----:com.apple.iTunes:SERIES-PART'))
    result['publisher']   = _mp4s(t.get('----:com.apple.iTunes:PUBLISHER'))
    result['subtitle']    = _mp4s(t.get('----:com.apple.iTunes:SUBTITLE'))
    result['language']    = _mp4s(t.get('----:com.apple.iTunes:LANGUAGE'))
    result['asin']        = _mp4s(t.get('----:com.apple.iTunes:ASIN'))
    result['isbn']        = _mp4s(t.get('----:com.apple.iTunes:ISBN'))

    trkn = t.get('trkn')
    if trkn and trkn[0]:
        result['track']       = str(trkn[0][0]) if trkn[0][0] else ''
        result['track_total'] = str(trkn[0][1]) if len(trkn[0]) > 1 and trkn[0][1] else ''

    disk = t.get('disk')
    if disk and disk[0]:
        result['disc'] = str(disk[0][0]) if disk[0][0] else ''

    covr = t.get('covr')
    if covr:
        result['cover_art'] = bytes(covr[0])

    return result


def _write_m4b(path: Path, fields: dict) -> None:
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    t = audio.tags

    def s(k): return fields.get(k)

    if s('title'):       t['\xa9nam'] = [s('title')]
    if s('author') or s('artist'): t['\xa9ART'] = [s('author') or s('artist')]
    album = s('album') or s('title')
    if album:            t['\xa9alb'] = [album]
    if s('narrator'):    t['aART']    = [s('narrator')]
    if s('composer'):    t['\xa9wrt'] = [s('composer')]
    if s('year') or s('date'): t['\xa9day'] = [s('year') or s('date')]
    if s('genre'):       t['\xa9gen'] = [s('genre')]
    if s('copyright'):   t['cprt']    = [s('copyright')]
    comment = s('comment') or s('description')
    if comment:          t['\xa9cmt'] = [comment]
    if s('grouping'):    t['\xa9grp'] = [s('grouping')]
    if s('series'):      t['----:com.apple.iTunes:SERIES']      = [s('series').encode()]
    if s('series_num'):  t['----:com.apple.iTunes:SERIES-PART'] = [str(s('series_num')).encode()]
    if s('publisher'):   t['----:com.apple.iTunes:PUBLISHER']   = [s('publisher').encode()]
    if s('subtitle'):    t['----:com.apple.iTunes:SUBTITLE']    = [s('subtitle').encode()]
    if s('language'):    t['----:com.apple.iTunes:LANGUAGE']    = [s('language').encode()]
    if s('asin'):        t['----:com.apple.iTunes:ASIN']        = [s('asin').encode()]
    if s('isbn'):        t['----:com.apple.iTunes:ISBN']        = [s('isbn').encode()]

    track = s('track')
    if track:
        total = int(s('track_total') or 0) if str(s('track_total') or '').isdigit() else 0
        t['trkn'] = [(int(track), total)]

    cover = s('cover_art')
    if cover:
        t['covr'] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]

    audio.save()


# ──────────────────────────────────────────────────────────────────
#  FLAC
# ──────────────────────────────────────────────────────────────────

_FLAC_MAP = {
    'title':       'title',
    'author':      'artist',
    'artist':      'artist',
    'album':       'album',
    'narrator':    'performer',
    'composer':    'composer',
    'year':        'date',
    'genre':       'genre',
    'publisher':   'organization',
    'comment':     'comment',
    'description': 'description',
    'series':      'series',
    'series_num':  'series-part',
    'track':       'tracknumber',
    'track_total': 'tracktotal',
    'disc':        'discnumber',
    'grouping':    'grouping',
    'subtitle':    'subtitle',
    'copyright':   'copyright',
    'language':    'language',
    'asin':        'asin',
    'isbn':        'isbn',
}


def _read_flac(path: Path) -> dict:
    result: dict = {}
    audio = FLAC(path)
    result['duration'] = audio.info.length
    t = audio.tags or {}

    def g(k): return (t.get(k.lower()) or [''])[0]

    _art  = g('artist')
    _perf = g('performer')
    _comp = g('composer')
    result['title']       = g('title')
    result['author']      = _art
    result['artist']      = _art
    result['album']       = g('album')
    result['narrator']    = (g('narrator')
                             or (_perf if _perf and _perf != _art else '')
                             or (_comp if _comp and _comp != _art else ''))
    result['composer']    = _comp
    result['year']        = g('date') or g('year')
    result['genre']       = g('genre')
    result['publisher']   = g('organization') or g('publisher')
    result['comment']     = g('comment')
    result['description'] = g('description')
    result['series']      = g('series')
    result['series_num']  = g('series-part') or g('series_num')
    result['grouping']    = g('grouping')
    result['track']       = g('tracknumber')
    result['track_total'] = g('tracktotal')
    result['disc']        = g('discnumber')
    result['subtitle']    = g('subtitle')
    result['copyright']   = g('copyright')
    result['language']    = g('language')
    result['asin']        = g('asin')
    result['isbn']        = g('isbn')

    if audio.pictures:
        result['cover_art'] = audio.pictures[0].data

    return result


def _write_flac(path: Path, fields: dict) -> None:
    audio = FLAC(path)
    for fk, tk in _FLAC_MAP.items():
        val = fields.get(fk) or (fields.get('album') if fk == 'album' and 'title' in fields else None)
        if fk == 'album':
            val = fields.get('album') or fields.get('title')
        if val is not None and str(val).strip():
            audio[tk] = [str(val)]

    cover = fields.get('cover_art')
    if cover:
        pic = Picture()
        pic.type = 3
        pic.mime = 'image/jpeg'
        pic.desc = 'Cover'
        pic.data = cover
        audio.clear_pictures()
        audio.add_picture(pic)

    audio.save()


# ──────────────────────────────────────────────────────────────────
#  OGG Vorbis / Opus  (Vorbis comments)
# ──────────────────────────────────────────────────────────────────

def _read_vorbis(audio, result: dict) -> dict:
    t = audio.tags or {}

    def g(k): return (t.get(k.lower()) or [''])[0]

    _art  = g('artist')
    _perf = g('performer')
    _comp = g('composer')
    result['title']       = g('title')
    result['author']      = _art
    result['artist']      = _art
    result['album']       = g('album')
    result['narrator']    = (g('narrator')
                             or (_perf if _perf and _perf != _art else '')
                             or (_comp if _comp and _comp != _art else ''))
    result['composer']    = _comp
    result['year']        = g('date') or g('year')
    result['genre']       = g('genre')
    result['publisher']   = g('organization') or g('publisher')
    result['comment']     = g('comment')
    result['description'] = g('description')
    result['series']      = g('series')
    result['series_num']  = g('series-part') or g('series_num')
    result['grouping']    = g('grouping')
    result['track']       = g('tracknumber')
    result['track_total'] = g('tracktotal')
    result['disc']        = g('discnumber')
    result['subtitle']    = g('subtitle')
    result['copyright']   = g('copyright')
    result['language']    = g('language')
    result['asin']        = g('asin')
    result['isbn']        = g('isbn')

    mbp = t.get('metadata_block_picture')
    if mbp:
        try:
            pic = Picture(base64.b64decode(mbp[0]))
            result['cover_art'] = pic.data
        except Exception:
            pass

    return result


def _write_vorbis(audio, fields: dict) -> None:
    for fk, tk in _FLAC_MAP.items():
        val = fields.get(fk)
        if fk == 'album':
            val = fields.get('album') or fields.get('title')
        if val is not None and str(val).strip():
            audio[tk] = [str(val)]

    cover = fields.get('cover_art')
    if cover:
        pic = Picture()
        pic.type = 3
        pic.mime = 'image/jpeg'
        pic.desc = 'Cover'
        pic.data = cover
        audio['metadata_block_picture'] = [base64.b64encode(pic.write()).decode('ascii')]

    audio.save()


def _read_ogg(path: Path) -> dict:
    audio = OggVorbis(path)
    return _read_vorbis(audio, {'duration': audio.info.length})


def _write_ogg(path: Path, fields: dict) -> None:
    _write_vorbis(OggVorbis(path), fields)


def _read_opus(path: Path) -> dict:
    audio = OggOpus(path)
    return _read_vorbis(audio, {'duration': audio.info.length})


def _write_opus(path: Path, fields: dict) -> None:
    _write_vorbis(OggOpus(path), fields)
