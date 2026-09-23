# Closest Certified-Zone Landing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a room session find a stationary floor phone and land at the closest currently admissible certified landing zone, otherwise observe and return home.

**Architecture:** A versioned room-site extension declares surveyed landing zones and clearance sources. Deterministic code ranks admissible zones using target/zone uncertainty and revalidates them before descent. Jev requests `LAND_NEAR`; it cannot certify, rank or waive a zone.

**Tech Stack:** JSON Schema, room spatial evidence, cube executor/supervisor, SQLite events, pytest.

**Approved design:** [Jev Agentic Room Session and Bounded Localization Recovery](../specs/2026-09-22-jev-agentic-room-design.md)

---

## File map

- `room/site.py`: extend the base surveyed site with landing zones; do not replace its home/corridor/contingency contract.
- `room/landing.py`: pure admissibility, uncertainty-aware ranking and deterministic fallback.
- `room/store.py`: append-only zone assurance, occupancy, revocation and clearance revisions.
- `room/candidates.py`, `controller.py`, `supervisor.py`: expose and execute a landing only while every revision remains current.

### Task 1: Define certified landing-zone site contracts

**Files:**
- Modify: `src/sentinel/room/site.py`
- Modify: `src/sentinel/room/store.py`
- Create: `docs/superpowers/specs/contracts/room-site-v1.schema.json`
- Create: `tests/room/test_site.py`
- Create: `fixtures/room_agent/v1/synthetic-landing-site.json`

- [ ] **Step 1: Write failing schema/parser tests**

Pin static `LandingZone(zone_id, polygon_cm, touchdown_cm, approach_volume, rotor_clearance_cm, person_clearance_cm, evidence_source, clearance_policy_id, max_clearance_age_ms, conditions, provenance)` and controlled-corridor declarations. At session setup or a qualifying observation, append `LandingClearanceRevision` with the active session/clock-domain IDs and deadlines from that session's injected monotonic clock. Absolute monotonic values never appear in the reusable site fixture; wall time remains audit metadata. Reject polygons outside/straddling the allowed surveyed footprint, obstacle/forbidden-volume intersections, undersized polygons, missing descent volume, stale/blank provenance and duplicate IDs. Test append-only occupancy, assurance, revocation and clearance revisions, including forward/backward wall-clock jumps, restart clock resets and cross-session reuse that cannot revive or import clearance.

```python
@dataclass(frozen=True)
class LandingZone:
    zone_id: str
    polygon_xy_cm: tuple[tuple[float, float], ...]
    touchdown_cm: tuple[float, float, float]
    approach_volume: Volume3D
    rotor_clearance_cm: float
    person_clearance_cm: float
    evidence_source: str
    clearance_policy_id: str
    max_clearance_age_ms: int
    conditions: tuple[str, ...]
    provenance: str

@dataclass(frozen=True)
class LandingClearanceRevision:
    revision_id: str
    zone_id: str
    session_id: str
    clock_domain_id: str
    state: Literal["CURRENT", "OCCUPIED", "REVOKED"]
    assured_at: str
    assured_monotonic_ms: int
    expires_monotonic_ms: int
    evidence_refs: tuple[str, ...]
```

Landing admission requires the latest revision for the same zone, session and clock domain, clamps its lifetime to the zone's `max_clearance_age_ms`, and treats expiry as derived state. Missing, foreign-session or foreign-clock-domain revisions make the zone inadmissible.

- [ ] **Step 2: Run tests and verify red**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_site.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: failures for the missing landing-zone parser/schema or revision store.

- [ ] **Step 3: Implement parser/schema without invented real measurements**

Tests use `synthetic-landing-site.json`. Plan 06 creates a separately named measured Flight-3 fixture only after pad geometry, clearance and assurance are declared; otherwise it commits a sanitized blocked-evidence index entry and leaves real landing disabled.

- [ ] **Step 4: Verify green and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_site.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: all selected tests pass.

```powershell
rtk git add src/sentinel/room/site.py src/sentinel/room/store.py docs/superpowers/specs/contracts/room-site-v1.schema.json tests/room/test_site.py fixtures/room_agent/v1/synthetic-landing-site.json
rtk git commit -m "feat(room): define certified landing zones"
```

### Task 2: Select the closest admissible zone robustly

**Files:**
- Create: `src/sentinel/room/landing.py`
- Create: `tests/room/test_landing.py`

- [ ] **Step 1: Write failing admissibility/ranking tests**

Define `LandingAssessment(zone_id, admissible, distance_interval_cm, reasons)` and `LandingSelection(status, zone_id, closest_proven, assessments)`. Test unique nearest, deterministic tie by zone ID, overlapping distance intervals (`CLOSEST_UNPROVEN`), expired/revoked/occupied zone, person exclusion, phone/rotor margin, approach obstruction, unavailable target location, stale target evidence, insufficient energy and no admissible zone.

```python
@dataclass(frozen=True)
class LandingAssessment:
    zone_id: str
    admissible: bool
    distance_interval_cm: tuple[float, float] | None
    reasons: tuple[str, ...]

@dataclass(frozen=True)
class LandingSelection:
    status: Literal["SELECTED", "CLOSEST_UNPROVEN", "NONE_ADMISSIBLE"]
    zone_id: str | None
    closest_proven: bool
    assessments: tuple[LandingAssessment, ...]
```

- [ ] **Step 2: Run tests and verify red**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_landing.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: import/interface failures for `room.landing`.

- [ ] **Step 3: Implement pure assessment**

Rank minimum target-to-touchdown distance only among certified, current, reachable zones. Propagate target and zone uncertainty. A forward-camera frame with no box is never clearance proof.

- [ ] **Step 4: Run the focused tests and verify green**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_landing.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_landing.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/landing.py tests/room/test_landing.py
rtk git commit -m "feat(room): select closest admissible landing zone"
```

### Task 3: Integrate landing candidates and final descent revalidation

**Files:**
- Modify: `src/sentinel/room/candidates.py`
- Modify: `src/sentinel/room/controller.py`
- Modify: `src/sentinel/room/supervisor.py`
- Create: `tests/room/test_phone_landing.py`

- [ ] **Step 1: Write failing end-to-end tests**

Test multi-view stationary floor phone, certified-zone selection, stale clearance just before descent, contradictory person detection, target moved after selection, battery/pose preemption, closest-unproven wording, no-zone fallback to safe observation plus home, and successful terminal `LANDED_AT_CERTIFIED_ZONE` only after descent completion.

- [ ] **Step 2: Run tests and verify red**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_phone_landing.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: landing candidates/controller integration are absent or capability-disabled.

- [ ] **Step 3: Enable capability behind explicit profile flag**

Session-scoped controlled-area assurance is created before execution and expires deterministically; it is not a per-action prompt. Revalidate target revision, zone state, clearance source, pose and reserve immediately before approach and descent. Any mismatch removes the candidate and feeds the refusal to the next turn.

- [ ] **Step 4: Run affected suite and verify green**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_site.py tests/room/test_landing.py tests/room/test_phone_landing.py tests/cube/test_step_executor.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: all selected tests pass; stale clearance produces no descent command.

- [ ] **Step 5: Commit**

```powershell
rtk git add src/sentinel/room/candidates.py src/sentinel/room/controller.py src/sentinel/room/supervisor.py tests/room/test_phone_landing.py
rtk git commit -m "feat(room): land at closest certified zone"
```

Independent reviewers must confirm no arbitrary floor point can become a landing candidate. Plan 06 promotes this capability only after a healthy-charge prepared-pad rehearsal.
