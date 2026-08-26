#!/usr/bin/env python3
"""Run the full Meta-Harness test suite without external dependencies.

Usage::

    python tests/run_all.py

Equivalent to ``python -m unittest tests.test_core``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(ROOT), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)