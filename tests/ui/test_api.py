from pathlib import Path
from fastapi.testclient import TestClient
from sentinel.ui.bootstrap import SessionConfig, build_service
from sentinel.ui.app import build_app
from sentinel.ui.models import validate_snapshot


def test_host_projection_and_capability_boundaries(tmp_path):
    config=SessionConfig(data_dir=tmp_path,scenario_path=Path('fixtures/mission-control/clean-computer.json'))
    service=build_service(config)
    try:
        with TestClient(build_app(service,repository_root=Path.cwd())) as client:
            assert client.get('/',follow_redirects=False).headers['location']=='/console/'
            for path in ['/console/','/console/app.mjs','/console/console.css','/assets/sentinel-ui/tokens/tokens.css']:
                assert client.get(path).status_code==200
            session=client.get('/api/v1/session').json()
            run=session['active_run_id']
            snapshot=client.get(f'/api/v1/runs/{run}/snapshot').json()
            validate_snapshot(snapshot)
            assert snapshot['source']['mode']=='REHEARSAL'
            assert snapshot['evidence']['provider'] == {
                'name': 'scripted', 'model': 'scenario', 'source_kind': 'SIMULATED'}
            assert client.get(f'/api/v1/runs/{run}/events?limit=201').status_code==422
            assert client.get('/api/v1/runs/other/snapshot').status_code==404
            assert client.get(f'/api/v1/runs/{run}/evidence/unknown').status_code==404
    finally:
        service.close()


def test_projection_uses_return_route_id_for_none_stop_index(tmp_path):
    config=SessionConfig(data_dir=tmp_path,scenario_path=Path('fixtures/mission-control/clean-computer.json'))
    service=build_service(config)
    try:
        run=config.run_id
        return_stop={
            'stop_id':'RETURN', 'option_id':'RETURN_OPTION', 'stop_index':None,
            'x_cm':0.0, 'y_cm':0.0, 'return_leg':True, 'status':'ACTIVE',
            'coverage':'OK', 'coverage_reason':'return leg',
            'predicted_tag_count':1, 'predicted_fix_error_cm':0.0,
            'leg_speed_cm_s':20.0, 'trim_budget':0, 'trim_progress':0,
            'arrival_error_cm':0.0, 'tolerance_cm':15.0,
            'tolerance_verdict':'INSIDE',
        }
        service.runtime.state[run]={
            'request_text':'return to start', 'phase':'FLIGHT',
            'updated_at':'2026-09-22T00:00:00+00:00',
            'stop':return_stop, 'stops':{'RETURN':return_stop},
        }
        snapshot=service.read_snapshot(run)
        assert snapshot['mission']['active_stop_id']=='RETURN'
        assert validate_snapshot(snapshot)==snapshot
    finally:
        service.close()
