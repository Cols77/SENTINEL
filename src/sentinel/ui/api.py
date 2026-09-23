"""Typed local routes; private errors never cross the HTTP boundary."""
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool


def build_router(service):
    router=APIRouter(prefix='/api/v1')
    def result(body):
        return JSONResponse(body,status_code={'CONFLICT':409,'INVALID':422,
            'IN_DOUBT':202,'UNAVAILABLE':503}.get(body.get('status'),200))
    @router.get('/site-profiles')
    def site_profiles(): return service.site_profiles()
    @router.post('/site-profiles/select')
    async def select_site_profile(request: Request):
        content_type = request.headers.get('content-type', '')
        if content_type.split(';', 1)[0].strip().lower() != 'application/json':
            return result(dict(status='INVALID',
                               reason_code='INVALID_PROFILE_REQUEST',
                               **service.site_profiles()))
        try:
            command = await request.json()
        except ValueError:
            command = None
        return result(await run_in_threadpool(service.select_site_profile, command))
    @router.get('/session')
    def session(): return service.session()
    @router.get('/runs/{run_id}/snapshot')
    def snapshot(run_id: str): return service.read_snapshot(run_id)
    @router.get('/runs/{run_id}/events')
    def events(run_id: str,after_sequence: int=Query(0,ge=0),limit: int=Query(100,ge=1,le=200)):
        return service.read_events(run_id,after_sequence,limit)
    @router.post('/runs/{run_id}/decisions')
    def decisions(run_id: str,command: dict): return result(service.submit_decision(run_id,command))
    @router.post('/runs/{run_id}/chat')
    def chat(run_id: str,command: dict): return result(service.submit_chat(run_id,command))
    @router.post('/runs/{run_id}/land')
    def land(run_id: str,command: dict): return result(service.request_land(run_id,command))
    @router.post('/safety/emergency')
    def emergency(command: dict): return result(service.trigger_emergency(command))
    @router.get('/runs/{run_id}/evidence/{artifact_id:path}')
    def evidence(run_id: str,artifact_id: str):
        cap=service.read_evidence(run_id,artifact_id)
        if cap.status=='AVAILABLE':
            return Response(cap.payload,media_type=cap.media_type,headers={'x-artifact-sha256':cap.sha256,'x-image-width':str(cap.width),'x-image-height':str(cap.height),'cache-control':'no-store'})
        return JSONResponse(dict(status=cap.status,run_id=run_id,artifact_id=artifact_id,reason=cap.reason_code),status_code={'FORBIDDEN':403,'REVOKED':410}.get(cap.status,404))
    return router
