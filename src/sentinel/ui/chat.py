"""Durable chat command handling, separate from the blocked mission worker."""
from .decision_session import SurveyDecisionSession
from .store import CommandConflict
from .mission_worker import MissionBusy

class ChatService:
    def __init__(self, service, session=None):
        self.service=service
        self.session=session or SurveyDecisionSession()

    def submit(self, run_id, command):
        svc=self.service;svc.require_run(run_id)
        required={'session_id','run_id','command_id','correlation_id','idempotency_key','expected_version','text'}
        conflict=dict(status='CONFLICT',kind='REFUSE',message='Command identity or state changed')
        if set(command)!=required or command['session_id']!=svc.session_id or command['run_id']!=run_id or type(command['expected_version']) is not int:
            return conflict
        if any(not isinstance(command[k],str) or not command[k] for k in required-{'expected_version'}):
            return conflict
        try:
            claim=svc.store.claim_command(command)
        except CommandConflict:
            return conflict
        if not claim['created']:
            return claim['command']['result'] or dict(status='IN_DOUBT',kind='ANSWER',message='Chat request is pending')
        snapshot=svc.read_snapshot(run_id)
        svc.store.add_message(run_id,'OPERATOR',command['text'],snapshot_version=snapshot['run']['version'])
        try:
            reply=self.session.respond(command['text'],snapshot)
            if reply.kind=='PROPOSE':
                row=svc.store.read_run(run_id)
                if row['status']!='ACTIVE' or svc.registry.closing or svc.registry.emergency.is_set() or row['decision_version']!=command['expected_version']:
                    result=conflict
                else:
                    svc.worker.start(dict(run_id=run_id,request_text=reply.request_text,correlation_id=command['correlation_id']))
                    result=dict(status='ACKNOWLEDGED',kind=reply.kind,message=reply.message)
            else:
                result=dict(status='ACKNOWLEDGED',kind=reply.kind,message=reply.message)
        except MissionBusy:
            result=dict(status='CONFLICT',kind='REFUSE',message='A mission is already active.')
        except Exception:
            result=dict(status='FAILED',kind='REFUSE',message='The request could not be completed. Your message was saved.')
        svc.store.add_message(run_id,'ASSISTANT',result['message'],status='FAILED' if result['status']=='FAILED' else 'COMPLETE',snapshot_version=snapshot['run']['version'])
        svc.store.append_event(run_id,'CHAT',{'summary':'Operator message and public response recorded'})
        return svc.store.settle_command(command['command_id'],result)
