"""App-wide constants: version, theme, colors, MIME types, search columns."""

import os as _os

APP_NAME    = "Audiobook Librarian"
APP_VERSION = "2.8.0"

# How many M4B builds may run at once. ffmpeg concat / stream-copy jobs are
# mostly I/O-bound, so running a few together finishes a batch much faster
# than one-at-a-time — but past a handful they just fight over the disk (or
# network share), so this is capped rather than scaled to the core count.
M4B_MAX_PARALLEL = 6

STYLE = """
QMainWindow,QWidget           { background:#1e1e2e; color:#cdd6f4; }
QTreeWidget                   { background:#181825; border:1px solid #313244; border-radius:4px; }
QTreeWidget::item             { padding:3px 4px; }
QTreeWidget::item:selected    { background:#313244; color:#89b4fa; }
QTreeWidget::item:hover       { background:#2a2a3e; }
QLineEdit,QComboBox           { background:#181825; border:1px solid #313244;
                                border-radius:4px; padding:4px 6px; }
QLineEdit:focus               { border-color:#89b4fa; }
QTextEdit                     { background:#181825; border:1px solid #313244;
                                border-radius:4px; padding:4px; }
QPushButton                   { background:#313244; border:1px solid #45475a;
                                border-radius:4px; padding:5px 12px; }
QPushButton:hover             { background:#45475a; }
QPushButton:pressed           { background:#89b4fa; color:#1e1e2e; }
QPushButton:disabled          { color:#585b70; background:#1e1e2e; }
QTableWidget                  { background:#181825; border:1px solid #313244;
                                gridline-color:#2a2a3e; }
QTableWidget::item:selected   { background:#2a3a4a; }
QHeaderView::section          { background:#1e1e2e; color:#a6adc8; border:none;
                                border-bottom:1px solid #313244; padding:4px 8px;
                                font-weight:bold; }
QScrollBar:vertical           { background:#181825; width:8px; border:none; }
QScrollBar::handle:vertical   { background:#45475a; border-radius:3px; }
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical { height:0; }
QSplitter::handle             { background:#313244; }
QToolBar                      { background:#181825; border-bottom:1px solid #313244;
                                spacing:2px; padding:3px; }
QStatusBar                    { background:#181825; color:#a6adc8; }
QGroupBox                     { border:1px solid #313244; border-radius:4px;
                                margin-top:8px; color:#a6adc8; }
QGroupBox::title              { subcontrol-origin:margin; left:8px; padding:0 4px; }
QProgressBar                  { border:1px solid #313244; border-radius:3px;
                                background:#181825; text-align:center; color:#cdd6f4; }
QProgressBar::chunk           { background:#89b4fa; border-radius:2px; }
QDialog                       { background:#1e1e2e; }
QMenu                         { background:#1e1e2e; border:1px solid #313244; }
QMenu::item:selected          { background:#313244; }
QCheckBox::indicator          { width:14px; height:14px; border:1px solid #45475a;
                                border-radius:3px; background:#181825; }
QCheckBox::indicator:checked  { background:#89b4fa; border-color:#89b4fa; }
QTabWidget::pane              { border:1px solid #313244; border-radius:4px; }
QTabBar::tab                  { background:#181825; border:1px solid #313244;
                                padding:6px 14px; margin-right:2px; border-radius:4px 4px 0 0; }
QTabBar::tab:selected         { background:#313244; color:#89b4fa; }
QTabBar::tab:hover            { background:#2a2a3e; }
QSpinBox                      { background:#181825; border:1px solid #313244;
                                border-radius:4px; padding:4px 6px; }
"""

BTN_PRIMARY  = "background:#89b4fa;color:#1e1e2e;font-weight:bold;padding:6px 18px;border-radius:4px;"

BTN_ACCENT   = "background:#f38ba8;color:#1e1e2e;font-weight:bold;padding:5px 12px;border-radius:4px;"

YELLOW       = "#f9e2af"

GREEN        = "#a6e3a1"

BLUE         = "#89b4fa"

GRAY         = "#a6adc8"

LAVENDER     = "#b4befe"

PEACH        = "#fab387"

RED          = "#f38ba8"

ORANGE       = "#fe640b"

CELL_PICKED  = "#1a3a1a"

MIME_FILES      = "application/x-audiobook-files"

MIME_BOOKNODES  = "application/x-audiobook-book-nodes"

MIME_FILEBLOCK  = "application/x-audiobook-file-block"

COL_TITLE, COL_AUTHOR, COL_NARRATOR, COL_SERIES, COL_SNUM, COL_YEAR, COL_PUB = range(7)

COL_NAMES = ["Title", "Author", "Narrator", "Series", "#", "Year", "Publisher"]

COL_KEYS  = ['title', 'author', 'narrator', 'series', 'series_num', 'year', 'publisher']
