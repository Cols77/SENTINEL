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
            assert client.get(f'/api/v1/runs/{run}/events?limit=201').status_code==422
            assert client.get('/api/v1/runs/other/snapshot').status_code==404
            assert client.get(f'/api/v1/runs/{run}/evidence/unknown').status_code==404
    finally:
        service.close()
