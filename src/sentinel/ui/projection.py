"""Allowlisted display facts derived from committed runtime records."""
from copy import deepcopy
import json
from .models import empty_snapshot, validate_snapshot


def stop_view(option,index,status='PLANNED'):
    return dict(id=f'{option.option_id}:{index}',status=status,range_m=option.range_cm/100,
        altitude_m=option.z_cm/100,extent_m=option.viewed_width_cm/100,duration_s=option.estimated_seconds/len(option.stops),resolvable_classes=list(option.resolvable_classes))

class ProjectionService:
    def __init__(self, service):
        self.service=service

    def snapshot(self, run_id):
        svc=self.service
        svc.require_run(run_id)
        with svc.runtime.lock:
            row=svc.store.read_run(run_id)
            state=deepcopy(svc.runtime.state.get(run_id,{}))
            facts=svc.runtime.facts(run_id)
            offer=svc.approval.current(run_id)
            s=empty_snapshot(svc.session_id,run_id,svc.config.mode)
            s['run'].update(request_text=state.get('request_text',facts['run']['request_text'] if facts['run'] else ''),status=row['status'],phase='APPROVAL' if offer else state.get('phase','REQUEST'),decision_version=row['decision_version'],updated_at=state.get('updated_at','1970-01-01T00:00:00+00:00'))
            s['source']['kind']=svc.adapters.source_kind
            s['chat']['messages']=svc.store.messages(run_id)
            s['safety']=deepcopy(svc.safety.state)
            s['safety']['land']['available']=svc.registry.get(run_id) is not None and not svc.registry.emergency.is_set()
            s['safety']['emergency']['available']=svc.safety.sender is not None
            for action in ('land','emergency'):
                if s['safety'][action]['available'] and s['safety'][action]['status']=='UNAVAILABLE':
                    s['safety'][action].update(status='IDLE',reason=None)
            options={o.option_id:o for o in svc.adapters.options}
            chosen=options.get(offer['candidate']['arguments']['option_id']) if offer else None
            last=facts['sweeps'][-1] if facts['sweeps'] else None
            option=chosen or (options[last['option_id']] if last else svc.adapters.options[0])
            if offer:
                s['proposal']=dict(id=offer['id'],sha256=offer['sha256'],option_id=option.option_id,stops=[stop_view(option,i) for i in range(len(option.stops))],range_m=option.range_cm/100,altitude_m=option.z_cm/100,extent_m=option.viewed_width_cm/100,duration_s=option.estimated_seconds,resolvable_classes=list(option.resolvable_classes),proposer=dict(mode='F',source=svc.adapters.source_kind),justification=offer['candidate'].get('description',''),guardrail_disagreements=[],target_object_ids=offer['candidate'].get('arguments',{}).get('addresses',[]),eligibility=dict(consent=True,envelope=True,preflight=True,current=True),disabled_reasons=[],run_id=run_id,decision_version=offer['decision_version'],correlation_id=offer['correlation_id'])
            route=[dict(stop_id=f'{option.option_id}:{i}',x=x,y=y) for i,(x,y) in enumerate(option.stops)]
            xs=[x for o in options.values() for x,y in o.stops];ys=[y for o in options.values() for x,y in o.stops]
            s['mission'].update(route=route,envelope=dict(min_x=min(xs),max_x=max(xs),min_y=min(ys),max_y=max(ys)),flights=dict(used=facts['results']['flights_used'],max=2))
            if last:
                s['mission']['sweep']=dict(id=last['sweep_id'],option_id=last['option_id'],status='COMPLETE' if last['status']=='FLOWN' else 'ACTIVE',merged=last['status']=='FLOWN',flights_used_before=max(0,len(facts['sweeps'])-1),stops=[stop_view(option,i,'COMPLETED' if last['status']=='FLOWN' else 'UNKNOWN') for i in range(len(option.stops))])
            if state.get('stop'):
                stop=state['stop'];s['mission']['active_stop_id']=f"{stop['option_id']}:{stop['stop_index']}"
                s['mission']['commanded_position']=dict(x=stop['x_cm'],y=stop['y_cm'])
            frame_meta={}
            sweeps={r['sweep_id']:r for r in facts['sweeps']}
            for f in facts['frames']:
                artifact=svc.artifacts.get(f['artifact_id'])
                status=svc.evidence_status.get(f['artifact_id'],'AVAILABLE' if artifact else 'MISSING')
                pose=dict(x_m=f['x_cm']/100,y_m=f['y_cm']/100,z_m=f['z_cm']/100,yaw_deg=f['heading_deg'])
                frame_meta[f['frame_id']]=artifact
                s['evidence']['frames'].append(dict(id=f['frame_id'],stop_id=f"{sweeps[f['sweep_id']]['option_id']}:{f['stop_index']}",captured_at=f['captured_at'],commanded_pose=pose if f['position_kind']=='COMMANDED' else None,measured_pose=pose if f['position_kind']=='MEASURED' else None,analysis='ANALYSED' if f['analysis_status']=='OK' else ('PENDING' if f['analysis_status']=='PENDING' else 'FAILED'),evidence=status,failure=None if f['analysis_status']=='OK' else f['analysis_status'],merge_eligible=f['analysis_status']=='OK',artifact_id=f['artifact_id'] if artifact else None,sha256=f['sha256'] if artifact else None,width=artifact['width'] if artifact else None,height=artifact['height'] if artifact else None,source_kind=svc.adapters.source_kind))
            for sight in facts['sightings']:
                box=json.loads(sight['bbox_json']) if sight['bbox_json'] else None
                artifact=frame_meta.get(sight['frame_id'])
                norm=None
                if box and artifact:
                    w,h=artifact['width'],artifact['height'];norm=[box['x']/w,box['y']/h,(box['x']+box['width'])/w,(box['y']+box['height'])/h]
                s['evidence']['sightings'].append(dict(id=sight['sighting_id'],frame_id=sight['frame_id'],class_name=sight['class'],bbox=norm,raw_score=sight['raw_score'],score_semantics=sight['score_kind'],provider=svc.config.detector_name,model=svc.adapters.source_kind))
            for o in facts['objects']:
                s['evidence']['objects'].append(dict(id=o['object_id'],class_name=o['class'],state=o['band'],sighting_ids=o['sighting_ids'],reason=f"{o['distinct_stops']} distinct observation stops",geometry_degraded=o['x_cm'] is None))
            for c in facts['results']['class_counts']:
                seen=[x for x in s['evidence']['sightings'] if x['class_name']==c['class']]
                unknown=c['method']=='NOT_OBSERVABLE' or not facts['frames']
                lower=c['method']=='SINGLE_VIEW_LOWER_BOUND'
                s['results']['counts'].append(dict(class_name=c['class'],semantics='UNKNOWN' if unknown else ('SINGLE_VIEW_LOWER_BOUND' if lower else 'RANGE'),confirmed=None if unknown else c['confirmed'],uncertain=None if unknown else c['uncertain'],lower_bound=c['uncertain'] if lower and not unknown else None,captured_at=facts['frames'][-1]['captured_at'] if facts['frames'] else None,observability='UNKNOWN' if not facts['frames'] else ('NOT_OBSERVABLE' if unknown else 'OBSERVABLE'),projected_px=None,min_px=None,reason=c['method'],arithmetic=None,sighting_ids=[x['id'] for x in seen],distinct_stops=None,geometry_degraded=False))
            analysed=sum(f['analysis_status']=='OK' for f in facts['frames'])
            s['evidence']['analysis'].update(analysed=analysed,total=len(facts['frames']))
            gaps=len(facts['frames'])-analysed
            if gaps:
                s['evidence']['failures'].append(f'{gaps} frame analyses unavailable; counts do not cover missing evidence')
                for count in s['results']['counts']:
                    if count['semantics']=='RANGE':
                        count.update(semantics='UNKNOWN',uncertain=None,observability='LIMITED',reason=f"{count['confirmed']} confirmed observations; upper bound unavailable due to analysis gaps")
            s['evidence']['provider'].update(name=svc.config.detector_name,model=svc.adapters.source_kind,source_kind=svc.adapters.source_kind)
            if facts['report']:
                s['report'].update(status='ACCEPTED',text=facts['report']['body'],id=facts['report']['report_id'])
            elif state.get('outcome',{}).get('status')=='REPORT_REJECTED':
                s['report'].update(status='REJECTED',reason='Report failed runtime validation')
            if svc.pose_feed:
                pose=svc.pose_feed.projection()
                if pose:s['localization']=pose
            s['legal_actions']=[dict(action=a,enabled=e,reasons=[] if e else ['Unavailable in current state']) for a,e in [('CHAT',True),('APPROVE',bool(offer)),('REJECT',bool(offer)),('LAND',s['safety']['land']['available']),('EMERGENCY',s['safety']['emergency']['available'])]]
            validate_snapshot(s)
            return svc.store.publish_snapshot(run_id,s)
