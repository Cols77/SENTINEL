"""A scripted camera boundary feeding real AprilTag estimation; no synthetic reports."""
from datetime import datetime,timezone
from pathlib import Path
from threading import Event, RLock, Thread
from uuid import uuid4
import cv2
import numpy as np
from sentinel.pose.detector import estimate_pose_with_evidence,NoTagDetected
from sentinel.pose.tagmap import load_tag_map
from .pose_projection import project_pose

class RehearsalPoseFeed:
    def __init__(self, run_id, artifact_root, fixture_root):
        self.run_id=run_id;self.root=Path(artifact_root);self.root.mkdir(parents=True,exist_ok=True)
        self.frame=cv2.imread(str(Path(fixture_root)/'pose-tags.png'))
        self.tag_map=load_tag_map(str(Path(fixture_root)/'pose-tag-map.json'))
        self.latest=None;self.artifacts={};self.lock=RLock();self.stop=Event()
        self.health=dict(frames_seen=0,reports_emitted=0,no_tag_frames=0,gated_frames=0,timeouts=0)
        self.thread=Thread(target=self._run,daemon=True,name='sentinel-rehearsal-camera');self.thread.start()

    def _run(self):
        tick=0
        while not self.stop.is_set():
            frame=self.frame if tick%6 in (0,1,5) else np.zeros_like(self.frame)
            try:
                identifier=str(uuid4())
                estimate=estimate_pose_with_evidence(frame,self.tag_map,run_id=self.run_id,report_id=identifier,
                    artifact_id=f'pose:{identifier}',captured_at=datetime.now(timezone.utc).isoformat())
                path=self.root/f'{identifier}.png';path.write_bytes(estimate.encoded_png)
                with self.lock:
                    self.latest=estimate
                    self.artifacts[estimate.evidence.artifact_id]=dict(path=path,sha256=estimate.evidence.sha256,width=estimate.evidence.width_px,height=estimate.evidence.height_px)
                    self.health['reports_emitted']+=1
                    while len(self.artifacts)>2:
                        oldest=next(iter(self.artifacts));self.artifacts.pop(oldest)['path'].unlink(missing_ok=True)
            except NoTagDetected:
                with self.lock:self.health['no_tag_frames']+=1
            except (ValueError,OSError):
                with self.lock:self.health['gated_frames']+=1
            with self.lock:self.health['frames_seen']+=1
            tick+=1
            self.stop.wait(1)

    def projection(self):
        with self.lock:
            if self.latest is None:return None
            # Explicit synthetic room-to-survey calibration, in centimetres.
            result=project_pose(self.latest,transform=lambda p:dict(x=p[0]*10-200,y=p[1]*10-430),health=dict(self.health))
            p=result['map_position'];result['containment']='INSIDE' if -150<=p['x']<=150 and 0<=p['y']<=100 else 'OUTSIDE'
            return result

    def close(self):
        self.stop.set();self.thread.join(timeout=2)
