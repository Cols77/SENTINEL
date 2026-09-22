"""Synthetic scene inputs, not outcomes; the normal survey loop derives every result."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageDraw
from sentinel.dry_run import DryRunClient
from sentinel.client import EmergencyAborted
from sentinel.runner import FlightTestRunner
from sentinel.survey.envelope import SweepEnvelope
from sentinel.survey.options import generate_options
from sentinel.survey.flight import FlightFrame
from sentinel.survey.detector import ScriptedDetector
from sentinel.survey.proposer import RuleProposer
from sentinel.survey.observability import SYNTHETIC_LEVEL, IMAGE_WIDTH, IMAGE_HEIGHT, PRINCIPAL_X, PRINCIPAL_Y, FOCAL_PX
from sentinel.mission_observer import (RunnerLifecycleEvent, StopProgressEvent, notify,
                                        runner_finished, runner_started)

@dataclass(frozen=True)
class ScenarioAdapters:
    options: tuple
    policy: object
    flight: object
    detector: object
    writer: object
    camera: object
    source_kind: str
    emergency_sender: object = None
    pose_feed: object = None
    location: str | None = None
    consent_date: str | None = None
    provenance_mode: str = 'F'

class ScenarioFlight:
    def __init__(self, frames, artifacts):
        self.frames, self.artifacts = frames, artifacts
        self.flights=0
        self.registry=None
        self.run_id=None
        self.emit=lambda *args:None
        self.observer=None

    def fly(self, option):
        runner=FlightTestRunner(DryRunClient(),'dry-run')
        if self.observer is not None:
            runner_started(self.observer, self.run_id, runner)
        elif self.registry:
            self.registry.bind(self.run_id,runner)
        notify(self.observer, RunnerLifecycleEvent(run_id=self.run_id, stage='MISSION', status='STARTED', runner=runner))
        self.flights+=1
        succeeded = False
        try:
            result=[]
            for frame in self.frames[option.option_id]:
                runner.check_interruption()
                payload=dict(stop_index=frame.stop_index,x_cm=frame.x_cm,y_cm=frame.y_cm,option_id=option.option_id,
                             coverage='OK',coverage_reason='Deterministic rehearsal coverage',
                             predicted_tag_count=1,predicted_fix_error_cm=0.0,
                             leg_speed_cm_s=20,trim_budget=0,trim_progress=0,
                             return_leg=False,arrival_error_cm=0.0,tolerance_cm=15.0,
                             tolerance_verdict='INSIDE', tag_height_cm=None,
                             height_cm=frame.z_cm, vertical_mismatch_cm=None)
                notify(self.observer, StopProgressEvent(run_id=self.run_id, stop_index=frame.stop_index,
                                                        stop=f"{option.option_id}_{frame.stop_index}",
                                                        option_id=option.option_id,
                                                        x_cm=frame.x_cm, y_cm=frame.y_cm,
                                                        status='ARRIVED', payload=payload))
                if self.observer is None:
                    self.emit('STOP_ARRIVED',payload)
                result.append(frame)
            succeeded = True
            return result
        except Exception as exc:
            status = 'EMERGENCY_ABORTED' if isinstance(exc, EmergencyAborted) else 'FAILED'
            notify(self.observer, RunnerLifecycleEvent(run_id=self.run_id, stage='MISSION', status=status,
                                                        message=str(exc), runner=runner))
            raise
        finally:
            if succeeded:
                notify(self.observer, RunnerLifecycleEvent(run_id=self.run_id, stage='MISSION', status='PASS', runner=runner))
            if self.observer is not None:
                runner_finished(self.observer, self.run_id, runner)
            elif self.registry:
                self.registry.unbind(self.run_id,runner)


def build_rehearsal_adapters(scenario_path, artifact_root):
    scene=json.loads(Path(scenario_path).read_text())
    root=Path(artifact_root);root.mkdir(parents=True,exist_ok=True)
    options=generate_options(SweepEnvelope(-150,150,0,100,80,180,300))
    frames,script,artifacts={},{},{}
    for option in options:
        frames[option.option_id]=[]
        for i,(x,y) in enumerate(option.stops):
            depth=scene['target_y_cm']-y
            u=PRINCIPAL_X+FOCAL_PX*(scene['target_x_cm']-x)/depth
            v=PRINCIPAL_Y+55*FOCAL_PX/depth
            detection=dict(label='laptop',confidence=scene['band'],description='a synthetic laptop',x=(u-20)/IMAGE_WIDTH,y=(v-20)/IMAGE_HEIGHT,width=40/IMAGE_WIDTH,height=40/IMAGE_HEIGHT)
            name=f'{option.option_id}-{i}.png'
            image=Image.new('RGB',(IMAGE_WIDTH,IMAGE_HEIGHT),'#18232c')
            draw=ImageDraw.Draw(image)
            draw.rectangle((u-20,v-20,u+20,v+20),fill='#7bb7c4',outline='white',width=2)
            draw.text((12,12),'REHEARSAL / SYNTHETIC SCENE',fill='white')
            path=root/name;image.save(path)
            payload=path.read_bytes();digest=hashlib.sha256(payload).hexdigest()
            artifact=f'{scene["id"]}:{option.option_id}:{i}'
            frames[option.option_id].append(FlightFrame(i,0,x,y,option.z_cm,0,'COMMANDED',str(path),artifact,digest,delta_z_cm=55))
            if scene.get('missing_stop')!=i:
                script[name]=[detection]
            artifacts[artifact]=dict(path=path,sha256=digest,width=IMAGE_WIDTH,height=IMAGE_HEIGHT)
    flight=ScenarioFlight(frames,artifacts)
    def writer(results):
        row=next(c for c in results['class_counts'] if c['class']=='computer')
        if row['uncertain']:
            return f"Between {row['confirmed']} and {row['confirmed']+row['uncertain']} computers; unresolved observations remain."
        return f"I found {row['confirmed']} computers."
    policy=ScenarioPolicy() if scene.get('follow_up') else RuleProposer()
    return ScenarioAdapters(options,policy,flight,ScriptedDetector(script),writer,SYNTHETIC_LEVEL,'SIMULATED')


class ScenarioPolicy(RuleProposer):
    """A scripted proposer selects only a candidate supplied by runtime legality."""
    def decide(self, request, context):
        if context.results.get('flights_used')==1 and context.results.get('uncertain_objects'):
            candidate=next((c for c in context.legal.candidates if c.tool_name=='survey.propose_sweep' and c.arguments.get('option_id')=='CLOSE_LOOK_CENTRE'),None)
            if candidate:
                return {'status':'TOOL_CALLS','tool_calls':[{'tool_name':candidate.tool_name,'arguments':{**candidate.arguments,'reason':candidate.description}}]}
        return super().decide(request,context)
