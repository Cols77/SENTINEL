"""Build the ROOM_DOORS_V1 evaluation pack and the provider profile that cites it.

    uv run python fixtures/perception-eval/room-doors-v1/build_pack.py

Reproducible from the two recorded flights: it hashes the eleven captured frames,
writes the manifest and the labels beside this file, derives the pack hash from the
manifest's own canonical bytes, and writes config/provider-profile-gemini-openrouter.v1.json
citing that hash. Everything it writes is validated against the WP0 contracts before
it lands. The frames themselves stay under artifacts/ (gitignored, they show people);
the pack identifies each one by sha256, not by path.
"""
import hashlib
import json
from pathlib import Path

from jsonschema import validate

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
CONTRACTS = ROOT / "docs/superpowers/specs/contracts"

#: The counted classes, in the order the flights sent them.
CLASSES = ("computer", "pen", "person", "phone")

#: case_id -> (frame path, truth, split). Truth is what is really in the frame,
#: read off the images on 2026-09-23, not what any detector answered. The object
#: at the foot of the doors is the phone the operator confirmed on 2026-09-22.
CASES = {
    # 2026-09-22 live-detection flight: the phone is on the ground by the doors
    "DOORS_0922_FAR_L_HI": ("20260922-163134-reanchor/frames/000000.png",
                            {"person": 1, "phone": 1}, "positive"),
    "DOORS_0922_NEAR_R_LO": ("20260922-163134-reanchor/frames/000001.png",
                             {"phone": 1}, "positive"),
    "DOORS_0922_FAR_R_HI": ("20260922-163134-reanchor/frames/000002.png",
                            {"phone": 1}, "positive"),
    "DOORS_0922_NEAR_L_LO": ("20260922-163134-reanchor/frames/000003.png",
                             {"phone": 1}, "positive"),
    "DOORS_0922_FAR_MID_HI": ("20260922-163134-reanchor/frames/000004.png",
                              {"phone": 1}, "positive"),
    # the frame's bottom edge cuts above the floor: the phone is out of shot
    "DOORS_0922_RETURN": ("20260922-163134-reanchor/frames/000005.png", {}, "negative"),
    # 2026-09-23 gate flight: one person, no phone in the room
    "DOORS_0923_FAR_L_HI": ("20260923-122157-reanchor/frames/000000.png",
                            {"person": 1}, "positive"),
    "DOORS_0923_NEAR_R_LO": ("20260923-122157-reanchor/frames/000001.png", {}, "negative"),
    "DOORS_0923_FAR_R_HI": ("20260923-122157-reanchor/frames/000002.png", {}, "negative"),
    "DOORS_0923_NEAR_L_LO": ("20260923-122157-reanchor/frames/000003.png",
                             {"person": 1}, "holdout"),
    "DOORS_0923_FAR_MID_HI": ("20260923-122157-reanchor/frames/000004.png",
                              {"person": 1}, "holdout"),
}


def canonical(document: dict) -> bytes:
    """The single byte projection every hash in this pack is taken over."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def prompt_revision() -> str:
    """The adapter's own prompt, rendered for CLASSES and hashed. Editing the prompt
    changes this string, which is the point: a profile names the prompt it was measured on."""
    from sentinel.survey.providers.gemini import CLASS_HINTS
    prompt = (
        "Detect every real, physical instance of: "
        + ", ".join(f"{name} ({CLASS_HINTS[name]})" if name in CLASS_HINTS else name
                    for name in CLASSES) + ". "
        "Ignore pictures, drawings, reflections and screens showing them. "
        'Return only a JSON object {"objects": [...]} whose items each have '
        '"label" (exactly one of the names above, without the words in brackets), '
        '"box_2d" as [ymin, xmin, ymax, xmax] scaled 0 to 1000, '
        '"confidence" as one of "high", "medium" or "low", and "description". '
        'If there are none, return {"objects": []}.')
    return "survey-detect-prompt+" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    trials = ROOT / "artifacts/trials"
    labels, cases = {}, []
    for case_id, (relative, truth, split) in CASES.items():
        frame = trials / relative
        if not frame.is_file():
            raise SystemExit(f"missing frame for {case_id}: {frame}")
        cases.append({"case_id": case_id, "artifact_sha256": sha256_file(frame),
                      "label_ref": f"labels.json#{case_id}", "split": split})
        labels[case_id] = {"counts": {name: truth.get(name, 0) for name in CLASSES},
                           "source_run": relative.split("/")[0]}
    manifest = {"manifest_version": "perception-evaluation-manifest.v1",
                "evaluation_pack_id": "ROOM_DOORS_V1",
                "created_at": "2026-09-23T00:00:00+00:00",
                "label_schema_revision": "survey-counts-v1",
                "cases": sorted(cases, key=lambda case: case["case_id"])}
    validate(manifest, json.loads((CONTRACTS / "evaluation-manifest-v1.schema.json")
                                  .read_text(encoding="utf-8")))
    pack_sha256 = hashlib.sha256(canonical(manifest)).hexdigest()

    profile = {
        "profile_version": "provider-profile.v1",
        "profile_id": "7f3c1e28-5a44-4b19-9c07-2d6e8a41b350",
        "approval_status": "APPROVED",
        "selection_decision_id": "COLIN-2026-09-23-SOLE-OPERATOR-APPROVAL",
        "provider": "gemini-openrouter",
        "model": "google/gemini-2.5-flash",
        "model_revision": "google/gemini-2.5-flash@2026-09-23-openrouter-resolved",
        "prompt_revision": prompt_revision(),
        "transform_revision": "fullframe+2x2-overlap0.15-cubic-960x720",
        "evaluation_pack_sha256": pack_sha256,
        "score_semantics": ("VLM_CONFIDENCE_BAND: this provider returns high/medium/low, "
                            "never a number. A sighting counts as confirmed at band high. "
                            "The numeric threshold below is the recorded equivalent of "
                            "DETECTOR_RAW_HIGH_PROVISIONAL and is measured for no provider."),
        "decision_threshold": 0.35,
        "replay_allowed_in_judged_path": False,
        "box_required": True,
    }
    validate(profile, json.loads((CONTRACTS / "provider-profile-v1.schema.json")
                                 .read_text(encoding="utf-8")))

    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (HERE / "labels.json").write_text(json.dumps(labels, indent=2, sort_keys=True) + "\n",
                                      encoding="utf-8")
    out = ROOT / "config/provider-profile-gemini-openrouter.v1.json"
    out.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    print(f"evaluation_pack_sha256 = {pack_sha256}")
    print(f"prompt_revision        = {profile['prompt_revision']}")
    print(f"{len(cases)} cases -> {HERE / 'manifest.json'}")
    print(f"profile -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
