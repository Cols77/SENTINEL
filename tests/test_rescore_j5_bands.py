from scripts.rescore_j5_bands import band_of, rescore

def test_band_of_uses_uncertain_interval():
    assert band_of(0.48, uncertain=(0.40, 0.60)) == "UNCERTAIN"
    assert band_of(0.84, uncertain=(0.40, 0.60)) == "CONFIDENT_TRUE"
    assert band_of(0.14, uncertain=(0.40, 0.60)) == "CONFIDENT_FALSE"

def test_rescore_escalates_uncertain_instead_of_false_safe():
    rows = [
        {"case_id": "pos-002", "repeat": 2, "reference": {"hazard_relevant": True},
         "answers": {"hazard_relevant": {"value": False, "probability": 0.48}}},
        {"case_id": "neg-001", "repeat": 0, "reference": {"hazard_relevant": False},
         "answers": {"hazard_relevant": {"value": False, "probability": 0.14}}},
        {"case_id": "amb-001", "repeat": 0, "reference": {"hazard_relevant": None},
         "answers": {"hazard_relevant": {"value": False, "probability": 0.29}}},
    ]
    out = rescore(rows, uncertain=(0.40, 0.60))
    assert out == {"false_safe": 0, "false_escalation": 0, "escalated": 1, "confident_correct": 1, "scored": 2}
