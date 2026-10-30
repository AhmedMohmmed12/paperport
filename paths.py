"""Keep sessions and logs out of the source checkout and executable folder."""
import os
from pathlib import Path


def asset_path(name):
    return Path(__file__).resolve().parent / 'assets' / name


def data_dir():
    override = os.environ.get('PAPERPORT_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'Paperport'
