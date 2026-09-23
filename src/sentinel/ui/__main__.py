from argparse import ArgumentParser
from pathlib import Path
import sys
from .bootstrap import SessionConfig, StartupError, build_service
from .app import build_app


def parse_config(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser=ArgumentParser(description='SENTINEL Mission Control',
                          allow_abbrev=False)
    parser.add_argument('--mode',choices=['rehearsal','live'],default='rehearsal')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8771)
    parser.add_argument('--scenario',dest='scenario_path',type=Path,default=Path('fixtures/mission-control/clean-computer.json'))
    parser.add_argument('--data-dir',type=Path,default=Path('artifacts/mission-control'))
    for flag in ('artifact-root','ui-db','survey-db','pose-db','provider-profile'):
        parser.add_argument('--'+flag,type=Path)
    parser.add_argument('--site',dest='site_path',type=Path)
    parser.add_argument('--site-profiles',dest='site_profiles_path',type=Path)
    parser.add_argument('--site-profile',dest='site_profile_id',default='CanningSt')
    parser.add_argument('--tag-map',dest='tag_map_path',type=Path)
    parser.add_argument('--pose',dest='pose_mode',choices=['off','passive','reanchor'],default='off')
    parser.add_argument('--detector',dest='detector_name',
                        choices=['scripted','replay','moondream','gemini','gemini-openrouter'])
    parser.add_argument('--location')
    parser.add_argument('--consent-date')
    parser.add_argument('--confirm',action='store_true')
    parser.add_argument('--i-authorise-paid-calls',dest='authorise_paid_calls',action='store_true')
    parser.add_argument('--local-ip')
    parser.add_argument('--tello-ip',default='192.168.10.1')
    parser.add_argument('--surface-z-cm',type=float)
    parser.add_argument('--start-x-cm',type=int)
    parser.add_argument('--start-y-cm',type=int)
    parser.add_argument('--sr063-amended',action='store_true')
    args=vars(parser.parse_args(argv));args['mode']=args['mode'].upper()
    legacy_physical_flags = ('--site', '--tag-map', '--pose-db', '--pose',
                             '--surface-z-cm', '--start-x-cm', '--start-y-cm')
    explicit_legacy = any(
        token == flag or token.startswith(flag + '=')
        for token in argv for flag in legacy_physical_flags)
    if (args['mode'] == 'LIVE' and args['site_profiles_path'] is None and
            not explicit_legacy):
        args['site_profiles_path'] = (Path(__file__).resolve().parents[3] /
                                     'config/mission-control-sites.v1.json')
    if args['detector_name'] is None:
        args['detector_name']='gemini-openrouter' if args['mode']=='LIVE' else 'scripted'
    return SessionConfig(**args)


def main(argv=None):
    config=parse_config(argv)
    try:service=build_service(config)
    except StartupError as exc:
        print(f'Mission Control refused startup: {exc}')
        return 2
    app=build_app(service,repository_root=Path(__file__).resolve().parents[3])
    from nicegui import ui
    import uvicorn
    ui.run_with(app,mount_path='/_host',show_welcome_message=False,tailwind=False)
    try:uvicorn.run(app,host=config.host,port=config.port)
    finally:service.close()
    return 0

if __name__=='__main__':
    raise SystemExit(main())
