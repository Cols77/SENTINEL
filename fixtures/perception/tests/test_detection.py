from sentinel_perception.detection import (Detection, clean, deduplicate,
                                           build_prompt, is_degenerate,
                                           is_valid_label)


def _d(label, query, score, box, sector=0):
    return Detection(label=label, query=query, score=score, box_xyxy=box,
                     frame_index=0, sector=sector)


def test_prompt_is_lowercase_period_separated():
    """Grounding DINO's text encoder requires this form; other forms silently
    reduce recall rather than erroring."""
    assert build_prompt(["Rock", "Loose Scree."]) == "rock. loose scree."


def test_frame_spanning_box_rejected():
    """Observed on real output: an ungrounded phrase returns the whole frame."""
    assert is_degenerate((0.5, 3.7, 959.5, 716.5), 960, 720) == "FRAME_SPANNING"
    assert is_degenerate((100, 100, 300, 400), 960, 720) is None


def test_tokenizer_fragments_rejected():
    """All three observed on real output from a four-query prompt."""
    q = ["rock", "loose scree", "boulder", "person"]
    assert is_valid_label("##ree", q) == "SUBWORD_FRAGMENT"
    assert is_valid_label("", q) == "EMPTY_LABEL"
    assert is_valid_label("looseree", q) == "UNMATCHED_FRAGMENT"
    assert is_valid_label("rock boulder", q) == "MERGED_QUERIES"
    assert is_valid_label("rock", q) is None
    assert is_valid_label("scree", q) is None      # clean substring is fine


def test_stuff_queries_routed_out():
    """Material regions have no object boundary; they belong in segmentation."""
    dets = [_d("loose scree", "loose scree", 0.35, (300, 80, 830, 430)),
            _d("rock", "rock", 0.52, (100, 100, 200, 200))]
    kept, reasons = clean(dets, 960, 720, ["rock", "loose scree"])
    assert [d.query for d in kept] == ["rock"]
    assert reasons["STUFF_QUERY"] == 1


def test_overlapping_boxes_collapse():
    """Three boxes on one chair must count as one chair."""
    dets = [_d("chair", "chair", 0.60, (100, 100, 300, 400)),
            _d("chair", "chair", 0.55, (105, 102, 298, 398)),
            _d("chair", "chair", 0.50, (700, 100, 900, 400))]
    assert len(deduplicate(dets)) == 2