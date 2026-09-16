# Perception video captures

Versioned captures for Dzivha’s rock-perception workflow live here.

- `yaw-scan-20260916.h264` is the original Tello H.264 elementary stream from the verified four-turn yaw scan, recorded without re-encoding.
- `yaw-scan-20260916.mp4` is a playback-friendly remux of that same stream; the H.264 file remains the canonical perception input.
- Git LFS is required for these binary files; do not place captures under `artifacts/`.
- Each capture must have a sibling JSON manifest containing the source path, capture conditions, decoder metadata, and SHA-256 hash.
- The raw stream is the source artifact. Dzivha can decode it with PyAV/FFmpeg and produce the image artifacts required by the WP3 perception contract.

The current canonical capture was produced from a supervised yaw-only run with:

```powershell
.venv\\Scripts\\python.exe -u -m sentinel yaw-scan --real --confirm `
  --local-ip 192.168.10.2 --tello-ip 192.168.10.1 `
  --video-path fixtures/perception/live/yaw-scan-20260916.h264 `
  --dwell-seconds 2 --turn cw:90 --turn cw:90 --turn cw:90 --turn cw:90
```

The yaw-scan controller sends no translation or tilt command. The sibling
manifest is bound to the raw H.264 hash; verify it before extracting frames
for Dzivha. Do not run another live capture without explicit authorization.
