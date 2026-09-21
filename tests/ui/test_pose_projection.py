import pytest
from sentinel.ui.pose_projection import freshness_status

@pytest.mark.parametrize('age,expected',[(None,'UNAVAILABLE'),(0,'FRESH'),(999,'FRESH'),(1000,'AGING'),(2000,'AGING'),(2001,'STALE')])
def test_backend_freshness_boundaries(age,expected):
    assert freshness_status(age,2000)==expected


def test_revoked_pose_capability_is_removed_but_fix_is_retained(tmp_path):
    from sentinel.ui.bootstrap import SessionConfig,build_service
    from sentinel.ui.models import empty_snapshot
    from sentinel.pose.detector import estimate_pose_with_evidence
    from sentinel.pose.tagmap import load_tag_map
    from sentinel.ui.pose_projection import project_pose
    import cv2
    from datetime import datetime,timezone
    service=build_service(SessionConfig(data_dir=tmp_path))
    service.pose_feed.close()
    estimate=estimate_pose_with_evidence(cv2.imread('fixtures/mission-control/pose-tags.png'),load_tag_map('fixtures/mission-control/pose-tag-map.json'),run_id='mission',report_id='r',artifact_id='a',captured_at=datetime.now(timezone.utc).isoformat())
    service.pose_feed.projection=lambda:project_pose(estimate)
    try:
        assert service.read_snapshot('mission')['localization']['pose_evidence']['artifact_id']=='a'
        service.evidence_status['a']='REVOKED'
        snapshot=service.read_snapshot('mission')
        assert snapshot['localization']['pose_evidence'] is None
        assert snapshot['localization']['latest_report']['report_id']=='r'
    finally:service.close()
