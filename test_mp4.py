"""Compatibility entry point so `python -m unittest test_mp4 -v` works.

The real test package lives in tests/test_mp4.py.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_mp4 import *  # noqa: F401,F403,E402


if __name__ == "__main__":
    unittest.main()
