"""Desktop and diagnostic entry points shared by all uv launch methods."""
from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys
from pathlib import Path

from . import __version__


def default_data_dir() -> Path:
    """Editable/source installs keep the ledger at the project root, never cwd."""
    package = Path(__file__).resolve().parent
    project = package.parent.parent
    if package.parent.name == 'src' and (project / 'pyproject.toml').is_file():
        return project
    # A wheel installed elsewhere must not write into site-packages.
    if sys.platform == 'win32':
        local = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    else:
        local = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    return local / 'AssetsStatistics'


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='资产账本 · 本地桌面应用')
    parser.add_argument('--version', action='version', version=f'资产账本 {__version__}')
    parser.add_argument('--data-dir', type=Path, help='指定数据目录，开发模式默认新项目根目录')
    parser.add_argument('--check', action='store_true', help='检查环境和数据，不启动界面、不修改数据库')
    args = parser.parse_args(argv)
    base = (args.data_dir if args.data_dir is not None else default_data_dir()).resolve()
    if args.check:
        import sqlite3
        import tkinter
        import _tkinter
        from .storage import read_records

        print(f'Python {sys.version.split()[0]} / Tk {tkinter.TkVersion} / Tcl {_tkinter.TCL_VERSION} / SQLite {sqlite3.sqlite_version}')
        print(f'Python executable: {sys.executable}')
        print(f'Data directory: {base}')
        for name in ('assets_v4.db', 'deposits.db', 'deposits.json'):
            if (base / name).exists():
                print(f'{name}: {len(read_records(base / name))} records, readable')
        return

    base.mkdir(parents=True, exist_ok=True)
    (base / 'logs').mkdir(exist_ok=True)
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler(base / 'logs' / 'assetbook.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler], format='%(asctime)s %(levelname)s %(message)s')
    if sys.platform == 'win32':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    import tkinter as tk
    from tkinter import messagebox
    from .storage import Store
    from .ui import App
    root = tk.Tk()
    root.withdraw()
    store = None
    try:
        store = Store(base)
        App(root, store)
        root.deiconify()
        root.mainloop()
    except Exception as error:
        logging.exception('Application failed')
        messagebox.showerror('资产账本启动失败', f'{error}\n\n请检查目录权限与数据库；原始数据仍在数据目录。', parent=root)
        if store:
            store.close()
        root.destroy()
        raise
