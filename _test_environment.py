"""Install one isolated data directory before any production module is imported.

Shared across unittest discovery/import orders. Refuse a process which already
cached production paths instead of silently writing to a running account.
"""
import os
from pathlib import Path
import sys
import tempfile

_DIRECTORY = tempfile.TemporaryDirectory(prefix='tennis-tests-')
DATA = _DIRECTORY.name
if 'iolib' in sys.modules:
    raise RuntimeError('Tests require a fresh process: iolib already cached data paths')
os.environ['KALSHI_DATA'] = DATA
# No test may inherit live discovery/score cache overrides either.
os.environ.pop('TENNIS_DISCOVERY_CACHE', None)
os.environ.pop('TENNIS_SCORE_CACHE', None)
