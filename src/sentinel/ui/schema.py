"""Closed projection schema shared with the browser without a build dependency."""
S = {'type': 'string'}
ID = {'type': 'string', 'minLength': 1}
N = {'type': 'number'}
U = {'type': 'number', 'minimum': 0}
I = {'type': 'integer', 'minimum': 0}
B = {'type': 'boolean'}

def enum(*values): return {'enum': list(values)}
def arr(item): return {'type': 'array', 'items': item}
def nullable(item): return {'anyOf': [item, {'type': 'null'}]}
def obj(**fields): return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}

POSE = obj(x_m=N, y_m=N, z_m=N, yaw_deg=N)
POINT = obj(x=N, y=N)
STOP = obj(id=ID, status=enum('PLANNED','ACTIVE','COMPLETED','FAILED','ABORTED','UNKNOWN'), range_m=U, altitude_m=U, extent_m=U, duration_s=U, resolvable_classes=arr(ID))
PROPOSER = obj(mode=enum('F','E','J'), source=S)
PROPOSAL = obj(id=ID, sha256=ID, option_id=ID, stops=arr(STOP), range_m=U, altitude_m=U, extent_m=U, duration_s=U, resolvable_classes=arr(ID), proposer=PROPOSER, justification=S, guardrail_disagreements=arr(S), target_object_ids=arr(ID), eligibility=obj(consent=nullable(B),envelope=nullable(B),preflight=nullable(B),current=B), disabled_reasons=arr(S), run_id=ID, decision_version=I, correlation_id=ID)
FRAME = obj(id=ID, stop_id=ID, captured_at=nullable(S), commanded_pose=nullable(POSE), measured_pose=nullable(POSE), analysis=enum('PENDING','ANALYSED','FAILED','NOT_RETAINED'), evidence=enum('AVAILABLE','MISSING','FORBIDDEN','REVOKED'), failure=nullable(S), merge_eligible=B, artifact_id=nullable(ID),sha256=nullable(ID),width=nullable(I),height=nullable(I),source_kind=enum('SIMULATED','REPLAYED','LIVE'))
BOX = {'type':'array','items':{'type':'number','minimum':0,'maximum':1},'minItems':4,'maxItems':4}
SIGHTING = obj(id=ID,frame_id=ID,class_name=ID,bbox=nullable(BOX),raw_score=nullable(N),score_semantics=S,provider=S,model=S)
OBJECT = obj(id=ID,class_name=ID,state=enum('CONFIRMED','UNCERTAIN'),sighting_ids=arr(ID),reason=S,geometry_degraded=B)
COUNT = obj(class_name=ID,semantics=enum('RANGE','SINGLE_VIEW_LOWER_BOUND','UNKNOWN'),confirmed=nullable(I),uncertain=nullable(I),lower_bound=nullable(I),captured_at=nullable(S),observability=enum('OBSERVABLE','LIMITED','NOT_OBSERVABLE','UNKNOWN'),projected_px=nullable(U),min_px=nullable(U),reason=S,arithmetic=nullable(S),sighting_ids=arr(ID),distinct_stops=nullable(I),geometry_degraded=B)
SAFETY = obj(available=B,status=enum('IDLE','REQUESTED','ACKNOWLEDGED','COMPLETED','UNAVAILABLE','FAILED'),reason=nullable(S),command_id=nullable(ID))
TAG = obj(tag_id=I,corners_xy_normalized={'type':'array','minItems':4,'maxItems':4,'items':{'type':'array','minItems':2,'maxItems':2,'items':{'type':'number','minimum':0,'maximum':1}}},status=enum('USED','DETECTED_NOT_USED'),reason_code=nullable(S),zone_id=nullable(ID),reprojection_error_px=nullable(U))
POSE_EVIDENCE = obj(run_id=ID,report_id=ID,artifact_id=ID,sha256=ID,captured_at=S,width_px=I,height_px=I,source_kind=enum('SIMULATED','REPLAYED','LIVE'),tags=arr(TAG))
REPORT_POSE = obj(run_id=ID,report_id=ID,observed_at=S,source_kind=enum('SIMULATED','REPLAYED','LIVE'),point=obj(x=N,y=N),heading_deg=N,altitude_m=nullable(N),method=S,tag_observations=arr(obj(tag_id=I,zone_id=ID,distance_m=U,reprojection_error_px=U)),reprojection_error_px=U)
SCHEMA = obj(
 schema_version={'const':2},
 session=obj(id=ID,mode=enum('REHEARSAL','LIVE'),startup_summary=obj(site_id=nullable(ID),pose_mode=enum('off','passive','reanchor'),detector_name=S)),
 run=obj(id=ID,request_text=S,status=enum('ACTIVE','COMPLETE','ABORTED','RECOVERY_REQUIRED'),phase=enum('REQUEST','PROPOSAL','APPROVAL','FLIGHT','ANALYSIS','REVIEW','REPORT'),version=I,decision_version=I,updated_at=S),
 source=obj(mode=enum('REHEARSAL','LIVE'),kind=enum('SIMULATED','REPLAYED','LIVE'),freshness=enum('FRESH','STALE')),
 safety=obj(land=SAFETY,emergency=SAFETY),
 chat=obj(messages=arr(obj(id=ID,run_id=ID,sequence=I,role=enum('OPERATOR','ASSISTANT','SYSTEM'),body=S,status=enum('COMPLETE','FAILED'),snapshot_version=I)),pending_command_id=nullable(ID)),
 proposal=nullable(PROPOSAL),
 mission=obj(flights=obj(used=I,max={'const':2}),sweep=nullable(obj(id=ID,option_id=ID,status=enum('PLANNED','ACTIVE','COMPLETE','ABORTED'),merged=B,flights_used_before=I,stops=arr(STOP))),envelope=nullable(obj(min_x=N,max_x=N,min_y=N,max_y=N)),route=arr(obj(stop_id=ID,x=N,y=N)),active_stop_id=nullable(ID),commanded_position=nullable(POINT)),
 localization=obj(status=enum('FRESH','AGING','STALE','UNAVAILABLE'),age_ms=nullable(U),stale_after_ms=U,latest_report=nullable(REPORT_POSE),recent_stream=obj(frames_seen=I,reports_emitted=I,no_tag_frames=I,gated_frames=I,timeouts=I),containment=enum('INSIDE','MARGIN','OUTSIDE','UNAVAILABLE'),pose_evidence=nullable(POSE_EVIDENCE),map_position=nullable(POINT)),
 evidence=obj(frames=arr(FRAME),sightings=arr(SIGHTING),objects=arr(OBJECT),analysis=obj(analysed=I,total=I,dropped_labels=arr(S),unknown_labels=arr(S)),provider=obj(name=S,model=S,source_kind=S),failures=arr(S)),
 results=obj(counts=arr(COUNT)),
 report=obj(status=enum('ABSENT','ACCEPTED','REJECTED'),text=nullable(S),reason=nullable(S),run_id=ID,id=nullable(ID)),
 events=obj(watermark=I),legal_actions=arr(obj(action=enum('CHAT','APPROVE','REJECT','LAND','EMERGENCY'),enabled=B,reasons=arr(S)))

)
