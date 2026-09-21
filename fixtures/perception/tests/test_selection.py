from sentinel_perception.choreography import segment
from sentinel_perception.config import ChoreographyConfig, QualityConfig
from sentinel_perception.evidence import EvidenceWriter, replay
from sentinel_perception.quality import FrameQuality
from sentinel_perception.selection import flag_relative_texture, keyframes, select


def _fq(i, motion, tex=5000.0):
    return FrameQuality(index=i, t_s=i / 30, motion=motion, tenengrad=tex,
                        laplacian_var=300.0, mean_luma=110.0, clipped_frac=0.0,
                        accepted=motion < 1.0)


def _scan(textures, dwell=90, rot=50):
    qs, i = [], 0
    for k, tex in enumerate(textures):
        for j in range(dwell):
            qs.append(_fq(i, 0.2, tex * (1.0 if j % 20 == 0 else 0.5))); i += 1
        if k < len(textures) - 1:
            for _ in range(rot):
                qs.append(_fq(i, 7.5, 3000.0)); i += 1
    return qs


def test_keyframe_plus_alternates():
    qs = _scan([6000, 12000, 13000, 2400, 6000])
    sel = select(qs, segment(qs, ChoreographyConfig()), ChoreographyConfig(),
                 alternates=2)
    assert len(keyframes(sel)) == 5
    assert len(sel) == 15


def test_alternates_are_separated():
    """Three consecutive frames would be useless as fallbacks."""
    qs = _scan([8000] * 3)
    sel = select(qs, segment(qs, ChoreographyConfig()), ChoreographyConfig(),
                 alternates=2, min_separation=10)
    s0 = sorted(s.frame_index for s in sel if s.sector == 0)
    assert all(b - a >= 10 for a, b in zip(s0, s0[1:]))


def test_bearings_follow_sector():
    qs = _scan([6000] * 5)
    assert [s.bearing_centre_deg for s in keyframes(
        select(qs, segment(qs, ChoreographyConfig()), ChoreographyConfig())
    )] == [0, 90, 180, 270, 0]


def test_low_texture_flagged_not_dropped():
    """The snowfield case: flat sectors must survive the gate."""
    qs = _scan([12000, 12000, 12000, 2000, 12000])
    sel = select(qs, segment(qs, ChoreographyConfig()), ChoreographyConfig())
    flag_relative_texture(sel, QualityConfig())
    flat = [s for s in keyframes(sel) if s.sector == 3][0]
    assert flat.usable
    assert "LOW_TEXTURE_REL" in flat.flags


def test_blind_sector_recorded():
    """Mild contamination: the dwell is still segmented as a dwell, but every
    frame fails the motion gate, so the sector is recorded as BLIND.

    Motion here must sit ABOVE QualityConfig.max_motion (1.0) but BELOW the
    rotate split, which segment() derives as lo + 0.25*(hi-lo) of the run's
    flow range. With rotations at 7.5 that split is ~2.0, so 1.5 lands in the
    band we want to exercise.
    """
    qs, i = [], 0
    for k in range(3):
        motion = 1.5 if k == 1 else 0.2
        for _ in range(90):
            qs.append(_fq(i, motion)); i += 1
        if k < 2:
            for _ in range(50):
                qs.append(_fq(i, 7.5)); i += 1

    sel = select(qs, segment(qs, ChoreographyConfig()), ChoreographyConfig())
    blind = [s for s in sel if not s.usable]
    assert len(blind) == 1
    assert blind[0].sector == 1
    assert "BLIND_SECTOR" in blind[0].flags


def test_severe_drift_caught_as_choreography_mismatch():
    """Severe contamination: the dwell reads as rotation and disappears from
    the phase list entirely. That must not pass silently -- it surfaces as a
    choreography mismatch instead, which is the other half of the guarantee
    that a lost sector is always visible somewhere in the output.
    """
    from sentinel_perception.choreography import check_choreography

    qs, i = [], 0
    for k in range(3):
        motion = 5.0 if k == 1 else 0.2
        for _ in range(90):
            qs.append(_fq(i, motion)); i += 1
        if k < 2:
            for _ in range(50):
                qs.append(_fq(i, 7.5)); i += 1

    cfg = ChoreographyConfig()
    check = check_choreography(segment(qs, cfg), cfg)
    assert check["dwells"] == 2          # middle dwell absorbed into rotation
    assert check["match"] is False       # and that is reported, not hidden


def test_evidence_replays(tmp_path):
    ev = EvidenceWriter(tmp_path / "e.jsonl")
    for n in range(3):
        ev.emit("frame_selected", "LIVE", {"sector": n})
    assert [r["seq"] for r in replay(tmp_path / "e.jsonl")] == [1, 2, 3]