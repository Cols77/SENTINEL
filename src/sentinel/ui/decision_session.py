"""Bounded conversation intent; survey policy retains legal candidate authority."""
from dataclasses import dataclass
import re

@dataclass(frozen=True)
class DecisionReply:
    kind: str
    message: str
    request_text: str | None = None

class SurveyDecisionSession:
    def __init__(self, responder=None):
        self.responder=responder

    def respond(self, text, snapshot):
        if not isinstance(text,str) or not text.strip() or len(text)>4000:
            return DecisionReply('REFUSE','Enter a request between 1 and 4000 characters.')
        words=set(re.findall(r'[a-z]+',text.lower()))
        if words & {'approve','takeoff','take','land','emergency','fly'}:
            return DecisionReply('REFUSE','Use the explicit approval or safety controls. Chat cannot authorize flight.')
        if words & {'status','progress','results','report'}:
            return DecisionReply('ANSWER',f"Snapshot {snapshot['run']['version']}: {snapshot['run']['phase'].lower()}. "+(snapshot['report']['text'] or 'No validated report yet.'))
        if self.responder:
            reply=self.responder(text,snapshot)
            if not isinstance(reply,DecisionReply) or reply.kind not in {'ANSWER','CLARIFY','PROPOSE','REFUSE'}:
                return DecisionReply('REFUSE','The responder returned an unsupported action.')
            if reply.kind=='PROPOSE' and (not reply.request_text or snapshot['run']['status']!='ACTIVE'):
                return DecisionReply('REFUSE','No new mission is available in this run.')
            return reply
        if words & {'count','many'} and words & {'computer','computers','laptop','laptops'} and not words & {'not','except','without','person','people','phone','phones','pen','pens'}:
            return DecisionReply('PROPOSE','Preparing a legal survey proposal for your review.',text)
        return DecisionReply('CLARIFY','This console supports counting computers. Ask “count the computers”, or ask for status.')
