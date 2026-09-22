"""Live boundaries for the same survey service; never substitutes replay adapters."""
from dataclasses import asdict
from datetime import date
import ipaddress
import json
import math
import os
from pathlib import Path
import socket
import sqlite3
from threading import RLock
from PIL import Image
from jsonschema import validate
from sentinel.camera import TELLO_MEASURED
from sentinel.cube.site import load_site
from sentinel.pose.tagmap import load_tag_map
from sentinel.pose.persistence import ensure_schema
from sentinel.runner import FlightTestRunner
from sentinel.survey.consent import consent_policy
from sentinel.survey.cube_bridge import CubeFlight, envelope_from_site
from sentinel.cube.coverage import CoverageModel, check_tag_map_matches_site
from sentinel.survey.live_detect import DetectionCache, LiveDetectionSupervisor
from sentinel.survey.options import generate_options
from sentinel.survey.proposer import RuleProposer
from sentinel.survey.store import SurveyStore
from .bootstrap import StartupError
from .scenarios import ScenarioAdapters
from .pose_projection import project_pose


def validate_config(config):
    if not config.confirm:raise StartupError('LIVE_CONFIRM_REQUIRED')
    if not getattr(config, 'authorise_paid_calls', False):raise StartupError('LIVE_PAID_CALL_AUTH_REQUIRED')
    for name in ('site_path','tag_map_path','pose_db','provider_profile','location','consent_date','local_ip'):
        if not getattr(config,name):raise StartupError(f'LIVE_{name.upper()}_REQUIRED')
    if config.detector_name not in {'gemini','moondream'}:raise StartupError('LIVE_PROVIDER_REQUIRED')
    if config.pose_mode not in {'off','passive','reanchor'}:raise StartupError('INVALID_POSE_MODE')
    if config.pose_mode=='reanchor' and not config.sr063_amended:raise StartupError('SR063_AMENDMENT_REQUIRED')
    if (config.start_x_cm is None)!=(config.start_y_cm is None):raise StartupError('BOTH_START_COORDINATES_REQUIRED')
    if config.pose_mode=='off' and config.start_x_cm is None:raise StartupError('MEASURED_START_REQUIRED')
    if config.surface_z_cm is None or not math.isfinite(config.surface_z_cm) or config.surface_z_cm<0:
        raise StartupError('MEASURED_SURFACE_REQUIRED')
    try:
        address=ipaddress.ip_address(config.local_ip)
        if address.version!=4 or address.is_unspecified or address.is_loopback:raise ValueError()
        ipaddress.IPv4Address(config.tello_ip)
        date.fromisoformat(config.consent_date)
        site=load_site(config.site_path)
        tag_map=load_tag_map(str(config.tag_map_path))
        check_tag_map_matches_site(tag_map, site, tolerance_cm=5.0)
        CoverageModel(site, tag_map.doc['camera_calibration'])
        env=envelope_from_site(site,300)
        generate_options(env,TELLO_MEASURED)
        if config.start_x_cm is not None and not (env.min_x_cm<=config.start_x_cm<=env.max_x_cm and env.min_y_cm<=config.start_y_cm<=env.max_y_cm):
            raise ValueError('start outside envelope')
        if config.surface_z_cm>=env.min_z_cm:raise ValueError('surface must be below survey')
    except Exception as exc:raise StartupError('INVALID_LIVE_SITE_OR_COVERAGE') from exc
    prefix=config.detector_name.upper()
    if not os.environ.get(prefix+'_API_KEY') or not os.environ.get(prefix+'_MODEL'):
        raise StartupError('LIVE_PROVIDER_ENV_REQUIRED')
    try:
        profile=json.loads(config.provider_profile.read_text())
        root=Path(__file__).resolve().parents[3]
        validate(profile,json.loads((root/'docs/superpowers/specs/contracts/provider-profile-v1.schema.json').read_text()))
        if profile['approval_status']!='APPROVED' or profile['provider']!=config.detector_name or profile['model']!=os.environ[prefix+'_MODEL']:
            raise ValueError('profile does not match selected provider')
    except Exception as exc:raise StartupError('APPROVED_PROVIDER_PROFILE_REQUIRED') from exc
    db=config.survey_db or config.data_dir/'survey.sqlite'
    if not db.is_file():raise StartupError('CONSENT_DATABASE_REQUIRED')
    store=SurveyStore.open(db)
    try:consent_policy(store,config.location,config.consent_date)
    except Exception as exc:raise StartupError('RECORDED_CONSENT_REQUIRED') from exc
    finally:store.connection.close()
    try:
        config.pose_db.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(config.pose_db) as conn:ensure_schema(conn)
    except Exception as exc:raise StartupError('POSE_DATABASE_UNAVAILABLE') from exc


def make_client(config):
    from sentinel.djitellopy_backend import DjitellopyClient
    return DjitellopyClient(local_ip=config.local_ip,tello_ip=config.tello_ip)


def check_emergency_transport(config):
    from sentinel.emergency import EmergencySender
    # Binding checks the selected interface without sending a motor command.
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:sock.bind((config.local_ip,0))
    return EmergencySender(local_ip=config.local_ip,tello_ip=config.tello_ip).send


class HttpSession:
    def post(self,url,*,headers,json,timeout):
        import httpx
        connect,read,total=timeout
        return httpx.post(url,headers=headers,json=json,timeout=httpx.Timeout(total,connect=connect,read=read))


class LivePoseFeed:
    def __init__(self,root,site):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.site=site;self.lock=RLock();self.artifacts={};self.latest=None;self.health=None

    def accept(self,estimate):
        path=self.root/f'{estimate.report.report_id}.png'
        path.write_bytes(estimate.encoded_png)
        with self.lock:
            self.latest=estimate
            e=estimate.evidence
            self.artifacts[e.artifact_id]=dict(path=path,sha256=e.sha256,width=e.width_px,height=e.height_px)
            while len(self.artifacts)>2:
                self.artifacts.pop(next(iter(self.artifacts)))['path'].unlink(missing_ok=True)

    def accept_health(self,health):
        with self.lock:self.health=asdict(health)

    def projection(self):
        with self.lock:
            if self.latest is None:return None
            result=project_pose(self.latest,transform=lambda p:dict(x=p[0]*10,y=p[1]*10),health=self.health)
            p=result['map_position'];s=self.site
            result['containment']='INSIDE' if s.cube_min_x_m*100<=p['x']<=s.cube_max_x_m*100 and s.cube_min_y_m*100<=p['y']<=s.cube_max_y_m*100 else 'OUTSIDE'
            return result

    def close(self):pass


class LiveFlight:
    def __init__(self,config,site,tag_map,pose_feed,supervisor_factory=None,coverage=None):
        self.config,self.site,self.tag_map,self.pose_feed=config,site,tag_map,pose_feed
        self.supervisor_factory=supervisor_factory
        self.coverage=coverage
        self.registry=None;self.run_id=config.run_id;self.emit=lambda *args:None
        self.observer=None
        self.artifacts={};self.start=(config.start_x_cm,config.start_y_cm)
        self._detection_cache = None
        self._supervisor = None
        self._supervisors = []

    def _new_supervisor(self, option):
        if self.supervisor_factory is not None:
            return self.supervisor_factory(self.config, option)
        config = self.config
        return LiveDetectionSupervisor(
            root=config.artifact_root or config.data_dir / 'images',
            run_id=self.run_id, detector_name=config.detector_name,
            consent_db=config.survey_db or config.data_dir / 'survey.sqlite',
            location=config.location, date=config.consent_date,
            tiles=getattr(config, 'detect_tiles', 'none'),
            authorised=getattr(config, 'authorise_paid_calls', False))

    def detection_for(self, frame, classes):
        """Read the follower's exact-frame cache; never call the provider."""
        if self._detection_cache is None:
            from sentinel.survey.detector import DetectionBatch
            return DetectionBatch((), 'ANALYSIS_GAP_MISSING', raw={
                'error': 'live detection cache unavailable', 'classes': list(classes)})
        return self._detection_cache.for_frame(frame, classes)

    def fly(self,option):
        config=self.config
        supervisor = self._new_supervisor(option)
        self._supervisor = supervisor
        self._supervisors.append(supervisor)
        # This is deliberately before client creation and before CubeFlight's
        # runner.  The first saved frame can therefore be consumed while the
        # drone is in flight, without coupling follower health to navigation.
        supervisor.start()
        client = None
        conn = None
        frames = None
        try:
            client=make_client(config)
            conn=sqlite3.connect(config.pose_db)
            def register(runner):
                if self.observer is None and self.registry:
                    self.registry.bind(self.run_id,runner)
            def unregister(runner):
                if self.observer is None and self.registry:
                    self.registry.unbind(self.run_id,runner)
            def stop(entry):
                if entry.get('waypoint','').startswith(option.option_id+'_') and entry.get('status')!='ABORTED_EN_ROUTE':
                    index=int(entry['waypoint'].rsplit('_',1)[1]);x,y=entry['commanded_cm'][:2]
                    if self.observer is None:
                        self.emit('STOP_ARRIVED',dict(stop_index=index,x_cm=x,y_cm=y,option_id=option.option_id))
            bridge=CubeFlight(client=client,site=self.site,mode='real',pose_mode=config.pose_mode,
                run_id=self.run_id,mission_log=config.data_dir/'mission.jsonl',capture_dir=supervisor.frames_dir,
                start_x_cm=self.start[0],start_y_cm=self.start[1],surface_z_cm=config.surface_z_cm,
                sr063_amended=config.sr063_amended,tag_map=self.tag_map,conn=conn,confirm=True,
                on_runner=None if self.observer is not None else register,
                on_runner_done=None if self.observer is not None else unregister,
                on_stop=None if self.observer is not None else stop,
                on_pose=None if self.observer is not None else self.pose_feed.accept,
                on_pose_health=None if self.observer is not None else self.pose_feed.accept_health,
                observer=self.observer,coverage=self.coverage)
            frames=bridge.fly(option)
            self.start=(bridge.start_x_cm,bridge.start_y_cm)
            for f in frames:
                path=Path(f.image_path)
                with Image.open(path) as image:w,h=image.size
                self.artifacts[f.artifact_id]=dict(path=path,sha256=f.sha256,width=w,height=h)
            return frames
        finally:
            try:
                if conn is not None: conn.close()
                if client is not None: client.close()
            finally:
                # A flight exception still reaches this drain.  Timeout/kill
                # is bounded and represented in DetectionCache, never raised
                # into the controlled-landing path.
                try:
                    self._detection_cache = supervisor.finish(frames)
                except Exception as exc:
                    self._detection_cache = DetectionCache(
                        self.run_id, (), 'SUPERVISOR_ERROR', f'{type(exc).__name__}: {exc}')

    def close(self):
        remaining = []
        for supervisor in self._supervisors:
            confirmed = supervisor.close()
            if confirmed is not True or getattr(supervisor, 'cleanup_pending', False):
                remaining.append(supervisor)
        self._supervisors = remaining
        self._supervisor = remaining[-1] if remaining else None


def report_writer(results):
    rows=[]
    plurals={'computer':'computers','person':'people','phone':'phones','pen':'pens'}
    for c in results['class_counts']:
        label=plurals[c['class']]
        if c['method']=='NOT_OBSERVABLE':rows.append(f'{label}: not observable.')
        elif c['method']=='SINGLE_VIEW_LOWER_BOUND':rows.append(f'At least {c["uncertain"]} {label}.')
        elif c['uncertain']:rows.append(f'Between {c["confirmed"]} and {c["confirmed"]+c["uncertain"]} {label}.')
        else:rows.append(f'I found {c["confirmed"]} {label}.')
    return ' '.join(rows)


def compose(config):
    site=load_site(config.site_path);tag_map=load_tag_map(str(config.tag_map_path))
    try:sender=check_emergency_transport(config)
    except Exception as exc:raise StartupError('INDEPENDENT_EMERGENCY_UNAVAILABLE') from exc
    client=None
    try:
        client=make_client(config)
        FlightTestRunner(client,'real',envelope=site.envelope()).preflight()
    except Exception as exc:raise StartupError('LIVE_PREFLIGHT_FAILED') from exc
    finally:
        if client is not None:client.close()
    # Coverage is an operational guard and must be admitted before any flight.
    try:
        coverage = CoverageModel.from_files(site, config.tag_map_path)
        check_tag_map_matches_site(tag_map, site, tolerance_cm=5.0)
    except Exception as exc:
        raise StartupError('LIVE_COVERAGE_REQUIRED') from exc
    from sentinel.survey.providers.gemini import GeminiDetector
    from sentinel.survey.providers.moondream import MoondreamDetector
    cls={'gemini':GeminiDetector,'moondream':MoondreamDetector}[config.detector_name]
    prefix=config.detector_name.upper()
    detector=cls(api_key=os.environ[prefix+'_API_KEY'],model=os.environ[prefix+'_MODEL'],session=HttpSession())
    feed=LivePoseFeed(config.data_dir/'pose-images',site)
    return ScenarioAdapters(generate_options(envelope_from_site(site,300),TELLO_MEASURED),RuleProposer(),
        LiveFlight(config,site,tag_map,feed,coverage=coverage),detector,report_writer,TELLO_MEASURED,'LIVE',sender,feed,config.location,config.consent_date)
