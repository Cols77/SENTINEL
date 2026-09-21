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

# Allow cross-package test helpers (tests.coordinator.harness) to be imported
# from tests/agent boundary tests; the repo root is not on sys.path by default.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# INTEGRATION NOTE (integration/survey-demo). WP5 added tests/__init__.py, which makes pytest put
# the repo root on sys.path instead of tests/. tests/test_cli_proposal_review.py imports its helper
# as ``from coordinator.p3_helpers import P3Run`` -- the only file in the suite using that bare
# form; thirteen others use ``from tests.coordinator...``. It resolved only while pytest happened to
# add tests/ itself. Keeping tests/ importable here lets both branches' tests run unmodified.
# Follow-up for that file's owner: switch it to the ``from tests.coordinator...`` convention, after
# which this block can go.
_TESTS_ROOT = str(Path(__file__).resolve().parent)
if _TESTS_ROOT not in sys.path:
    sys.path.append(_TESTS_ROOT)
