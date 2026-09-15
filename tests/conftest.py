"""Keep the test suite pointed at this checkout's src tree.

The development machine may have another SENTINEL checkout installed in an
editable environment.  Tests must exercise the source tree that owns them.
"""

from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

# A previous editable checkout may have been imported by the host test
# environment before pytest loaded this conftest. Remove that package and its
# submodules so collection resolves this checkout consistently.
for module_name in tuple(sys.modules):
    if module_name == "sentinel" or module_name.startswith("sentinel."):
        del sys.modules[module_name]

# Import once here so every test module receives the same checkout-owned
# package object, including when another editable checkout is installed.
importlib = __import__("importlib")
sentinel_package = importlib.import_module("sentinel")
assert Path(sentinel_package.__file__).resolve().is_relative_to(SOURCE_ROOT)
