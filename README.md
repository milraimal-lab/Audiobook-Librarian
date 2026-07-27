# Audiobook Librarian
(Warning: Vibe Coded)
A Windows desktop app for wrangling a messy audiobook collection into a clean,
tagged, consistently-organized library. Built with PyQt6 + mutagen.

Point it at a **Library** folder (and optionally an **Import** folder for new
downloads), and it gives you:

## Features

- **Two-tree workflow** — an Import inbox and your Library side by side.
  Drag a book onto an author/series node and it's filed: files move into
  `Author\Series\## Title\`, get renamed consistently, and tags are written —
  one gesture.
- **Metadata editing** — per-field apply (safe for multi-selection), tag
  pickers that suggest values from the author's other books, batch title
  parsing ("The Novice (Summoner, #1)" → title/series/number), cover
  management with full-size preview.
- **Metadata search** — Audible catalog (narrator + series data) and Open
  Library, with per-cell or whole-row picking, applied and saved in one step.
- **Merge & split** — combine multi-folder books into one; split
  folder-glued collections apart manually or automatically by album tag.
- **Duplicate detection** — same author+title books are flagged red in the
  tree; the checker compares file sizes, then MD5, then a **tag-agnostic
  audio-stream hash** that sees through differing tags/covers. Delete either
  copy straight to the Recycle Bin from the results.
- **Problems panel** — unreadable files are surfaced with suggested fixes and
  a one-click lossless **ffmpeg repair** (rebuilds the container, audio
  untouched, original goes to the Recycle Bin).
- **M4B builder** — turn a folder of MP3s into a single chaptered .m4b
  (chapters from filenames) using the bundled ffmpeg.
- **Safety throughout** — files are **never overwritten** (collisions skip or
  auto-rename), deletes go to the Recycle Bin, every file operation is
  undoable and written to a session log, and emptied junk folders (thumbnails,
  .nfo …) are cleaned up automatically.

## Running from source

```
pip install -r requirements.txt
python main.py
```

Optional: place `ffmpeg.exe` next to `main.py` (or have it on PATH) to enable
Build M4B and Repair.

## Building the exe

```
build.bat
```

Produces `dist\AudiobookLibrarian.exe` and bundles `ffmpeg.exe` beside it
(copied from PATH or downloaded on first build). Ship the two files together.

## Tests

```
pip install pytest
python -m pytest tests
```

The tagger tests generate real MP3/M4B files with ffmpeg and are skipped if
ffmpeg isn't available.

## Code layout

| Module | Contents |
| --- | --- |
| `main.py` | entry point |
| `mainwindow.py` | main window, toolbar, orchestration |
| `tabs.py` | Edit Metadata / Files / Move-Organise tabs |
| `booktree.py` | the author→series→book tree with drag-drop |
| `dialogs.py` | search, problems, duplicates, settings dialogs |
| `workers.py` | background threads (scan, save, move, hash, repair, m4b) |
| `scanner.py` | folder scanning and book grouping |
| `tagger.py` | tag read/write across MP3/M4B/FLAC/OGG/Opus |
| `organizer.py` | folder layout and safe file moves |
| `audible.py` / `openlibrary.py` | metadata sources |
| `constants.py` / `util.py` | theme, version, shared helpers |

## Notes

- Settings live in `~\.audiobookmanagerv2.json`, the session log in
  `~\.audiobookmanagerv2.log`.
- Windows-only in a few places (Recycle Bin, Explorer integration); the core
  logic is portable.
