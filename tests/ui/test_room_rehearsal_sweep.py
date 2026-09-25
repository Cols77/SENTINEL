"""Checkpoint frames in rehearsal retain the view they were captured from."""

from sentinel.pose.replay import FrameRecorder
from sentinel.ui.room_rehearsal import (
    FakeTelloWorld, FakeVideo, SceneDetector, SceneObject, Scenario,
)


def test_scene_detector_uses_each_saved_sweep_frames_heading(tmp_path):
    world = FakeTelloWorld()
    world.height_dm = 10
    video = FakeVideo(world)
    recorder = FrameRecorder(tmp_path)
    paths = []
    for heading in (60, 90, 120):
        world.heading_deg = heading
        paths.append(recorder.save(video.read(timeout=0)))
    scenario = Scenario("sweep", "sweep", "sweep", (
        # On the ground plane, far enough ahead to be in frame from 1 m up.
        SceneObject("phone", "phone", 290, 450, 8, 4),
    ))
    detector = SceneDetector(scenario, video, world)
    batches = [detector.detect(str(path), ("phone",)) for path in paths]
    assert [len(batch.detections) for batch in batches] == [1, 1, 0]
    assert batches[0].detections[0]["bbox"]["x"] != batches[1].detections[0]["bbox"]["x"]
