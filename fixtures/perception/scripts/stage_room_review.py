"""Cache the manifest-pinned Grounding DINO checkpoint before a room flight."""
from __future__ import annotations

import json
from pathlib import Path

from huggingface_hub import snapshot_download


def main() -> int:
    manifest = json.loads(Path("models/manifest.json").read_text(encoding="utf-8"))
    detector = manifest["detector_openvocab"]
    model_id, revision = detector["id"], detector["sha"]
    snapshot_download(repo_id=model_id, revision=revision)
    print(f"Staged {model_id}@{revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
