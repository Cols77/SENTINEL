from argparse import ArgumentParser
from pathlib import Path
from .bootstrap import SessionConfig, build_service
from .app import build_app


def main():
    parser=ArgumentParser(description='SENTINEL Mission Control')
    parser.add_argument('--mode',choices=['rehearsal','live'],default='rehearsal')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8771)
    parser.add_argument('--scenario',type=Path,default=Path('fixtures/mission-control/clean-computer.json'))
    parser.add_argument('--data-dir',type=Path,default=Path('artifacts/mission-control'))
    args=parser.parse_args()
    config=SessionConfig(mode=args.mode.upper(),host=args.host,port=args.port,scenario_path=args.scenario,data_dir=args.data_dir)
    service=build_service(config)
    app=build_app(service,repository_root=Path(__file__).resolve().parents[3])
    from nicegui import ui
    import uvicorn
    ui.run_with(app,mount_path='/_host',show_welcome_message=False,tailwind=False)
    try:
        uvicorn.run(app,host=args.host,port=args.port)
    finally:
        service.close()

if __name__=='__main__':
    main()
