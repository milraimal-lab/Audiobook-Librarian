"""
organizer.py – Rename templates and folder organisation logic.

Folder structure:  <base> / Author / Series / ## Book Title
File naming:       Book Title ##.ext      (e.g. "Alloy of Law 01.mp3")

If a book has no series, the Series level is skipped:
  <base> / Author / Book Title / Book Title ##.ext
"""

import re
import shutil
from pathlib import Path
from typing import Optional


# ──────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────

# Characters forbidden in Windows filenames/folder names
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(name: str) -> str:
    """Strip Windows-invalid characters and trim dots/spaces."""
    name = _FORBIDDEN.sub('', name)
    name = name.strip('. ')
    return name or 'Unknown'


def pad_num(num_str: str, width: int = 2) -> str:
    """Zero-pad the whole part to *width* digits, keeping any real fraction:
    '3' → '03',  '18.5' → '18.5',  '3.5' → '03.5',  '8.0' → '08'.
    A trailing all-zero decimal is dropped. Non-numeric strings are
    returned unchanged."""
    s = str(num_str).strip()
    try:
        float(s)
    except (ValueError, TypeError):
        return s
    whole, dot, frac = s.partition('.')
    try:
        base = f"{int(whole):0{width}d}"
    except ValueError:
        return s
    # keep the fraction only if it isn't all zeros ('.0' is meaningless)
    return f"{base}.{frac}" if (dot and frac.strip('0')) else base


# ──────────────────────────────────────────────────────────────────
#  Public API
# ──────────────────────────────────────────────────────────────────

def build_file_name(book_title: str, track_num: str, track_total: str = '') -> str:
    """
    Return the stem (no extension) for a track file.

    Rules:
      1 file  → no number at all         "Alloy of Law.mp3"
      2–9     → single digit, no pad     "Alloy of Law 3.mp3"
      10+     → zero-padded to width     "Alloy of Law 03.mp3"
    """
    title = sanitize(book_title)
    try:
        total = int(track_total) if track_total else 1
        num   = int(track_num)
        if total <= 1:
            return title                          # single file — no number
        elif total <= 9:
            return f"{title} {num}"               # 2-9 files  — bare digit
        else:
            width = len(str(total))               # 10+ files  — zero-padded
            return f"{title} {num:0{width}d}"
    except (ValueError, TypeError):
        return title


def build_folder_path(base_dir: str,
                      author:     str,
                      series:     str,
                      series_num: str,
                      book_title: str) -> Path:
    """
    Compose the destination folder.

    With series:    base / Author / Series / 04 Book Title
    Without series: base / Author / Book Title
    """
    base  = Path(base_dir)
    auth  = sanitize(author)      if author     else 'Unknown Author'
    title = sanitize(book_title)  if book_title else 'Unknown Title'

    if series:
        ser = sanitize(series)
        if series_num:
            book_folder = f"{pad_num(series_num)} {title}"
        else:
            book_folder = title
        return base / auth / ser / book_folder
    else:
        return base / auth / title


def preview_moves(books: list,
                  dest_dir: str) -> list[tuple[Path, Path]]:
    """
    Return a list of (src, dst) Path pairs for all files across all books,
    without actually moving anything.
    """
    dest = Path(dest_dir)
    moves: list[tuple[Path, Path]] = []

    for book in books:
        if not book.title:
            continue
        folder = build_folder_path(
            str(dest), book.author, book.series, book.series_num, book.title
        )
        total = book.file_count
        for i, af in enumerate(book.files, 1):
            stem = build_file_name(book.title, str(i), str(total))
            dst  = folder / (stem + af.ext)
            moves.append((af.path, dst))

    return moves


def apply_moves(moves: list[tuple[Path, Path]],
                copy: bool = False,
                skip_existing: bool = True,
                progress_cb=None) -> tuple[int, list[str], list[tuple[Path, Path]]]:
    """
    Perform the file moves (or copies).

    Existing destination files are NEVER overwritten: with skip_existing the
    source file stays put; otherwise the incoming file is auto-renamed with
    a ' (2)' style suffix.

    Returns (done, errors, performed, skipped, renamed):
      performed — (src, dst) pairs that actually happened (undo log)
      skipped   — (src, dst) collisions left in place
      renamed   — (src, final_dst) pairs that got a new name to avoid
                  replacing an existing file
    """
    done      = 0
    errors    = []
    performed = []
    skipped   = []
    renamed   = []
    total     = len(moves)

    for src, dst in moves:
        if progress_cb:
            progress_cb(done, total, f"{'Copying' if copy else 'Moving'} {src.name}…")

        if src == dst:
            done += 1
            continue

        final = dst
        if dst.exists():
            if skip_existing:
                skipped.append((src, dst))
                done += 1
                continue
            n = 2
            while True:
                cand = dst.with_name(f"{dst.stem} ({n}){dst.suffix}")
                if not cand.exists():
                    final = cand
                    break
                n += 1

        try:
            final.parent.mkdir(parents=True, exist_ok=True)
            if copy:
                shutil.copy2(str(src), str(final))
            else:
                shutil.move(str(src), str(final))
            performed.append((src, final))
            if final != dst:
                renamed.append((src, final))
        except Exception as exc:
            errors.append(f"{src.name} → {final.name}: {exc}")

        done += 1

    return done, errors, performed, skipped, renamed
