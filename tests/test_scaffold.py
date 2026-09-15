import os
from pathlib import Path
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _project_config():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pytest_search_paths_include_src_and_tests():
    pytest_config = _project_config()["tool"]["pytest"]["ini_options"]

    assert pytest_config["testpaths"] == ["src", "tests"]


def test_project_metadata_readme_exists():
    project_config = _project_config()["project"]

    assert (ROOT / project_config["readme"]).is_file()


def test_declared_cli_entry_point_is_callable():
    from sentinel.cli import main

    assert main() == 0


def test_module_entrypoint_exits_successfully():
    environment = os.environ.copy()
    source_path = str(ROOT / "src")
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        path for path in (source_path, existing_pythonpath) if path
    )

    result = subprocess.run(
        [sys.executable, "-m", "sentinel"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
