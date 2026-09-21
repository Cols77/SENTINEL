"""Backend-clock freshness and site-aware coordinate projection."""
from dataclasses import asdict
from datetime import datetime, timezone


def freshness_status(age_ms, stale_after_ms):
    if age_ms is None: return 'UNAVAILABLE'
    if age_ms>stale_after_ms: return 'STALE'
    return 'AGING' if age_ms>=stale_after_ms/2 else 'FRESH'


def project_pose(estimate, *, clock=lambda:datetime.now(timezone.utc), stale_after_ms=2000, transform=None, health=None):
    r=estimate.report
    age=max(0,(clock()-r.observed_at).total_seconds()*1000)
    report=dict(run_id=r.run_id,report_id=r.report_id,observed_at=r.observed_at.isoformat(),source_kind=r.source_kind,
        point=dict(x=r.point[0],y=r.point[1]),heading_deg=r.heading_deg,altitude_m=r.altitude_m,method=r.method,
        tag_observations=[asdict(t) for t in r.tag_observations],reprojection_error_px=r.reprojection_error_px)
    evidence=asdict(estimate.evidence)
    evidence['tags']=[{**asdict(t),'corners_xy_normalized':[list(p) for p in t.corners_xy_normalized]} for t in estimate.evidence.tags]
    map_position=transform(r.point) if transform else None
    return dict(status=freshness_status(age,stale_after_ms),age_ms=round(age),stale_after_ms=stale_after_ms,
        latest_report=report,pose_evidence=evidence,map_position=map_position,containment='UNAVAILABLE',
        recent_stream=health or dict(frames_seen=1,reports_emitted=1,no_tag_frames=0,gated_frames=0,timeouts=0))
