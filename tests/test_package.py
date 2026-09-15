from __future__ import annotations

import sentinel


def test_package_import_exposes_version() -> None:
    assert sentinel.__version__ == "0.1.0"

