"""Synthetic test: no fixture video needed, so this runs in CI."""

from sentinel_perception.choreography import segment, check_choreography
from sentinel_perception.config import ChoreographyConfig
from sentinel_perception.quality import FrameQuality


def _fq(i, motion):
    return FrameQuality(index=i, t_s=i / 30, motion=motion, tenengrad=5000.0,
                        laplacian_var=300.0, mean_luma=110.0, clipped_frac=0.0,
                        accepted=motion < 1.0)


def test_recovers_alternating_pattern():
    """Build a synthetic 4-turn scan and check we get 5 dwells, 4 rotations."""
    qualities, i = [], 0
    for turn in range(5):
        for _ in range(60):                    # 2 s dwell
            qualities.append(_fq(i, 0.2)); i += 1
        if turn < 4:
            for _ in range(56):                # ~1.9 s rotation
                qualities.append(_fq(i, 7.5)); i += 1

    phases = segment(qualities, ChoreographyConfig())
    check = check_choreography(phases, ChoreographyConfig())

    assert check["dwells"] == 5
    assert check["rotations"] == 4
    assert check["match"] is True
    assert check["closes_loop"] is True
    assert [p.index for p in phases if p.kind == "DWELL"] == [0, 1, 2, 3, 4]


def test_detects_missing_turn():
    """A refused turn must show up as a MISMATCH, not pass silently."""
    qualities, i = [], 0
    for turn in range(4):
        for _ in range(60):
            qualities.append(_fq(i, 0.2)); i += 1
        if turn < 3:
            for _ in range(56):
                qualities.append(_fq(i, 7.5)); i += 1

    check = check_choreography(segment(qualities, ChoreographyConfig()),
                               ChoreographyConfig())
    assert check["match"] is False
    assert check["rotations"] == 3