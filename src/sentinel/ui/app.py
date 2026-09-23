"""Local FastAPI host, compatible with NiceGUI's supported ASGI composition."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from .api import build_router


def build_app(service, *, repository_root):
    app=FastAPI(title='SENTINEL Mission Control',docs_url=None,redoc_url=None)
    app.include_router(build_router(service))
    # Task C: the room-agent architecture (additive; its own routes/module).
    from .room_agent import attach_room_agent, build_room_agent_router
    app.include_router(build_room_agent_router(attach_room_agent(service)))
    app.mount('/console',StaticFiles(directory=repository_root/'examples/survey-console',html=True),name='console')
    app.mount('/assets/sentinel-ui',StaticFiles(directory=repository_root/'assets/sentinel-ui'),name='assets')
    @app.get('/',include_in_schema=False)
    def index(): return RedirectResponse('/console/',status_code=307)
    @app.exception_handler(KeyError)
    async def missing(request,exc): return JSONResponse({'code':'NOT_FOUND','message':'Resource unavailable'},status_code=404)
    @app.exception_handler(ValueError)
    async def invalid(request,exc): return JSONResponse({'code':'INVALID_REQUEST','message':'Request rejected'},status_code=400)
    return app
