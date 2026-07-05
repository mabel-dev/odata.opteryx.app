"""Pytest configuration for odata.opteryx tests.

Sets up sys.path to include opteryx-core and other dependencies.
"""

import os
import sys

# Add opteryx-core to path for tests
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
opteryx_core_path = os.path.abspath(os.path.join(root_dir, "..", "opteryx-core"))
pyiceberg_path = os.path.abspath(os.path.join(root_dir, "..", "pyiceberg-firestore-gcs"))

if opteryx_core_path not in sys.path:
    sys.path.insert(0, opteryx_core_path)
if pyiceberg_path not in sys.path:
    sys.path.insert(0, pyiceberg_path)
