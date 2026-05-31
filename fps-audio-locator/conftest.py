"""Ensure the project root is importable as the package root during tests.

Lets ``import core`` / ``import apps`` work without an editable install when
running ``pytest`` from the project directory.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
