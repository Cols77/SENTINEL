# Proposed survey UI projection v1

This is a fixture-only contract, not an adopted coordinator transport. `contract.mjs` documents the complete field types and rejects extra internal fields recursively.

Methods: `read_survey(run_id)`, `read_survey_events(run_id, after_sequence, limit)`, `read_evidence(run_id, frame_id)`, `submit_decision(attempt)`. All return promises. Limits are 1–200; events are ordered after the cursor and no later than the current snapshot watermark. Missing runs raise NOT_FOUND; evidence failures are tagged results.

LIVE and REPLAY are fixture semantics. The existing kit uses REPLAYED; a backend owner must adopt an explicit mapping. Counts, flight budget, observability and merging are supplied facts, not reconstructed by the UI. Current-sweep progress counts only frames present in the projection. Every referenced frame, stop and sighting must be present.
