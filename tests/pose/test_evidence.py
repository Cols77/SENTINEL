from datetime import datetime,timezone
import hashlib
import cv2
from sentinel.pose.detector import estimate_pose_with_evidence
from sentinel.pose.tagmap import TagMap
from tests.pose.test_joint_pnp import _scene
from tests.pose.rendering import SPLAYED_PAIR,make_doc


def test_diagnostic_binds_report_corners_and_exact_encoded_image():
    _,_,frame=_scene()
    result=estimate_pose_with_evidence(frame,TagMap(make_doc(SPLAYED_PAIR)),run_id='r',report_id='p',artifact_id='image',captured_at=datetime.now(timezone.utc).isoformat())
    assert result.report.report_id==result.evidence.report_id=='p'
    assert hashlib.sha256(result.encoded_png).hexdigest()==result.evidence.sha256
    assert len(result.evidence.tags)==2
    assert all(t.status=='USED' for t in result.evidence.tags)
    assert all(0<=v<=1 for t in result.evidence.tags for xy in t.corners_xy_normalized for v in xy)


def test_single_tag_solution_keeps_detected_but_unused_corners():
    _,_,frame=_scene()
    result=estimate_pose_with_evidence(frame,TagMap(make_doc(SPLAYED_PAIR)),run_id='r',report_id='p',artifact_id='image',captured_at=datetime.now(timezone.utc).isoformat(),joint=False)
    assert [t.status for t in result.evidence.tags].count('USED')==1
    assert [t.status for t in result.evidence.tags].count('DETECTED_NOT_USED')==1


def test_display_sink_never_blocks_pose_producer():
    from threading import Event
    from sentinel.pose.evidence import LatestDisplaySink
    entered,release=Event(),Event()
    def callback(value):
        entered.set();release.wait(2)
    sink=LatestDisplaySink(callback)
    try:
        sink.push(1);assert entered.wait(1)
        for n in range(100):sink.push(n)
        assert sink.pending_count<=1
    finally:
        release.set();sink.close()
