"""Check which model checkpoints are reachable and record what we resolved.
"""

from __future__ import annotations

import json
from pathlib import Path

from huggingface_hub import model_info

# Ordered best-first. The first that resolves wins.
CANDIDATES = {
    "detector_openvocab": [
        "IDEA-Research/grounding-dino-base",
        "IDEA-Research/grounding-dino-tiny",
    ],
    "depth_metric": [
        "depth-anything/Depth-Anything-V2-Metric-Outdoor-Large-hf",
        "depth-anything/Depth-Anything-V2-Metric-Outdoor-Base-hf",
        "depth-anything/Depth-Anything-V2-Metric-Outdoor-Small-hf",
    ],
    "depth_relative": [
        "depth-anything/Depth-Anything-V2-Large-hf",
        "depth-anything/Depth-Anything-V2-Base-hf",
        "depth-anything/Depth-Anything-V2-Small-hf",
    ],
    "segmenter": [
        "facebook/sam2.1-hiera-large",
        "facebook/sam2-hiera-large",
        "facebook/sam2-hiera-base-plus",
    ],
}


def probe(repo_id: str) -> dict | None:
    try:
        info = model_info(repo_id)
        return {
            "id": repo_id,
            "sha": info.sha,
            "downloads": getattr(info, "downloads", None),
            "pipeline_tag": getattr(info, "pipeline_tag", None),
        }
    except Exception as exc:                      # noqa: BLE001
        print(f"    unavailable: {repo_id}  ({type(exc).__name__})")
        return None


def main() -> int:
    resolved = {}
    for role, ids in CANDIDATES.items():
        print(f"\n{role}:")
        for repo_id in ids:
            got = probe(repo_id)
            if got:
                print(f"    RESOLVED  {repo_id}  @{got['sha'][:8]}")
                resolved[role] = got
                break
        else:
            print(f"    NONE RESOLVED for {role}")

    out = Path("models/manifest.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(resolved, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())