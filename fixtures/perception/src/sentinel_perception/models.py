from __future__ import annotations

import gc
import json
from contextlib import contextmanager
from pathlib import Path

import torch

MANIFEST = Path("models/manifest.json")


def load_manifest() -> dict:
    if not MANIFEST.exists():
        raise FileNotFoundError(
            f"{MANIFEST} not found -- run: uv run python scripts/probe_models.py"
        )
    return json.loads(MANIFEST.read_text())


def pick_device() -> torch.device:
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def pick_dtype(device: torch.device) -> torch.dtype:
    return torch.float16 if device.type == "cuda" else torch.float32


def vram_gb() -> tuple[float, float]:
    """(allocated, total) in GB. Returns (0, 0) on CPU."""
    if not torch.cuda.is_available():
        return 0.0, 0.0
    return (
        torch.cuda.memory_allocated() / 1e9,
        torch.cuda.get_device_properties(0).total_memory / 1e9,
    )


def free() -> None:
    """Release everything the previous stage held.
    """
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()


@contextmanager
def stage(name: str, verbose: bool = True):
    """Run one model stage, guaranteeing cleanup even on failure."""
    free()
    before, total = vram_gb()
    if verbose and total:
        print(f"[{name}] start -- VRAM {before:.2f}/{total:.1f} GB")
    try:
        yield
    finally:
        peak = torch.cuda.max_memory_allocated() / 1e9 if torch.cuda.is_available() else 0.0
        free()
        after, _ = vram_gb()
        if verbose and total:
            print(f"[{name}] done  -- peak {peak:.2f} GB, now {after:.2f} GB")