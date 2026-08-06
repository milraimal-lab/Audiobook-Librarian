#!/usr/bin/env python3
"""Libwright — entry point.

A PyQt6 audiobook library manager: scan, tag, organize, merge/split,
deduplicate, repair, and build M4Bs.
"""

import sys

from PyQt6.QtWidgets import QApplication

from constants import STYLE, APP_NAME, APP_VERSION
from util import _rotate_log, log_line
from mainwindow import MainWindow


def main():
    _rotate_log()
    log_line(f"=== {APP_NAME} v{APP_VERSION} started ===")
    app = QApplication(sys.argv)
    app.setApplicationName(f"{APP_NAME} v{APP_VERSION}")
    app.setOrganizationName("ABMv2")
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
