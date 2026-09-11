"""Online SQLite backup on encrypted EBS. No tokens or database rows printed."""
import os
from pathlib import Path
import sqlite3
import time


def backup(source=Path('/data/state.sqlite'), directory=Path('/data/backups')):
    os.umask(0o077)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / (time.strftime('%Y%m%dT%H%M%S', time.gmtime()) + '.sqlite')
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Backup integrity check failed')
    for old in sorted(directory.glob('*.sqlite'))[:-7]:
        old.unlink()
    return target


if __name__ == '__main__':
    backup()
    print('SQLite online backup integrity verified; retained on encrypted EBS')
