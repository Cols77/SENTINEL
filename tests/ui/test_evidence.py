from pathlib import Path
from sentinel.ui.bootstrap import SessionConfig,build_service


def test_only_captured_run_bound_artifacts_are_served_and_revoke(tmp_path):
    svc=build_service(SessionConfig(data_dir=tmp_path))
    run=svc.config.run_id
    future=svc.worker.start(dict(run_id=run,request_text='count computers',correlation_id='corr'))
    try:
        assert svc.approval.changed.wait(2)
        proposal=svc.approval.current(run)
        artifact=next(iter(svc.artifacts))
        assert svc.read_evidence(run,artifact).status=='NOT_FOUND'
        svc.submit_decision(run,dict(session_id=svc.session_id,run_id=run,proposal_id=proposal['id'],proposal_sha256=proposal['sha256'],expected_version=proposal['decision_version'],decision='APPROVE',command_id='c',correlation_id='corr',attempt_id='a',idempotency_key='k'))
        assert future.result(3)['status']=='REPORTED'
        snapshot=svc.read_snapshot(run)
        artifact=snapshot['evidence']['frames'][0]['artifact_id']
        cap=svc.read_evidence(run,artifact)
        assert cap.status=='AVAILABLE' and cap.payload.startswith(b'\x89PNG')
        svc.evidence_status[artifact]='REVOKED'
        assert svc.read_evidence(run,artifact).payload is None
        svc.evidence_status.pop(artifact)
        svc.artifacts[artifact]['path'].write_bytes(b'changed')
        assert svc.read_evidence(run,artifact).status=='NOT_FOUND'
    finally:
        svc.close()
