# Perception video captures

Versioned captures for Dzivha’s rock-perception workflow live here.

- `*.h264` is the original Tello H.264 elementary stream, recorded without re-encoding.
- Git LFS is required for these binary files; do not place captures under `artifacts/`.
- Each capture must have a sibling JSON manifest containing the source path, capture conditions, decoder metadata, and SHA-256 hash.
- The raw stream is the source artifact. Dzivha can decode it with PyAV/FFmpeg and produce the image artifacts required by the WP3 perception contract.

Capture from a supervised run with:

```powershell
.venv\\Scripts\\python.exe -u -m sentinel run --real --confirm `
  --local-ip 192.168.10.2 --tello-ip 192.168.10.1 `
  --video-path fixtures/perception/live/tello-flight.h264
```

The default run still stops video before takeoff. Supplying `--video-path` explicitly retains the stream through shutdown and records the flight stream. Do not use this option for an unauthorized live run.
