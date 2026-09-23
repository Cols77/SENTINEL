# Live Site Profiles and Runtime Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Live Mission Control require a valid named physical site profile at startup, allow safe idle profile switching without restarting the host, refresh readiness from current facts, and treat proposal approval as the real-flight/paid-call confirmation.

**Architecture:** A focused `site_profiles.py` module owns registry parsing, path resolution, physical validation, and public catalogue projection. Bootstrap resolves one valid profile before binding HTTP, while `MissionControlService` owns atomic idle-only switching and on-read readiness refresh. Dedicated profile endpoints keep snapshot schema v3 closed; the browser consumes those endpoints and renders only current blockers.

**Tech Stack:** Python 3.11 dataclasses, FastAPI, SQLite, jsonschema, existing SENTINEL cube/pose/survey adapters, browser-native ES modules, Node test runner, pytest.

---

## File map

- Create `src/sentinel/ui/site_profiles.py`: registry types, validation, path resolution, physical config application, and allowlisted public catalogue.
- Create `config/mission-control-sites.v1.json`: portable `CanningSt` configuration plus disabled `Demo` draft.
- Modify `src/sentinel/ui/__main__.py`: add `--site-profiles` and `--site-profile`; retain legacy physical flags only as an explicit compatibility path.
- Modify `src/sentinel/ui/bootstrap.py`: resolve the selected physical profile before composition and fail Live startup when it is invalid.
- Modify `src/sentinel/ui/readiness.py`: dynamic network discovery, current-fact blocker reporting, and removal of startup confirmation/paid-call gates.
- Modify `src/sentinel/ui/live.py`: authorize the supervised detector only after an approved dispatch and preserve preflight-before-takeoff ordering.
- Modify `src/sentinel/ui/service.py`: refresh readiness on reads and atomically switch valid profiles only while pristine/idle.
- Modify `src/sentinel/ui/api.py`: add profile catalogue and switch routes without changing existing `/api/v1` routes or snapshot schema.
- Modify `examples/survey-console/http-client.mjs`: typed profile catalogue and switch calls.
- Modify `examples/survey-console/mission-app.mjs`: profile selector, actual blockers only, and live approval wording.
- Modify `examples/survey-console/approval.mjs`: state that Live approval dispatches the real flight.
- Modify `examples/survey-console/view-model.mjs`: remove the obsolete command-template view field.
- Modify `examples/survey-console/console.css`: restrained selector/status styling with narrow-layout support.
- Modify `README.md` and `examples/survey-console/README.md`: named-profile launch and calibration workflow.
- Test in `tests/ui/test_site_profiles.py`, `tests/ui/test_bootstrap.py`, `tests/ui/test_api.py`, `tests/ui/test_mission_worker.py`, `tests/survey_console/http-client.test.mjs`, `tests/survey_console/status.test.mjs`, and `tests/survey_console/approval.test.mjs`.

### Task 1: Registry contract and startup gate

**Files:**
- Create: `src/sentinel/ui/site_profiles.py`
- Create: `config/mission-control-sites.v1.json`
- Create: `tests/ui/test_site_profiles.py`
- Modify: `src/sentinel/ui/bootstrap.py`
- Modify: `src/sentinel/ui/__main__.py`
- Modify: `tests/ui/test_bootstrap.py`

- [ ] **Step 1: Write failing registry tests**

Cover: paths resolved relative to the registry; enabled profiles require site, tag map, pose DB, pose mode, surface height, and start coordinates for `off`; site/tag-map/coverage geometry is validated; disabled drafts require only ID/label/status; additional entries are accepted without code changes; the public catalogue exposes no filesystem paths.

```python
def test_registry_resolves_enabled_and_projects_disabled(tmp_path):
    registry = write_registry(tmp_path, profiles=[valid_entry('CanningSt'), {
        'id': 'Demo', 'label': 'Demo venue', 'enabled': False,
        'setup_status': 'Venue calibration required',
    }])
    resolved = load_site_profiles(registry)
    assert resolved.require('CanningSt').site_path.is_absolute()
    assert resolved.public('CanningSt')['profiles'][1] == {
        'id': 'Demo', 'label': 'Demo venue', 'enabled': False,
        'setup_status': 'Venue calibration required',
    }
    assert 'site_path' not in resolved.public('CanningSt')['profiles'][0]
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/ui/test_site_profiles.py tests/ui/test_bootstrap.py -q`

Expected: collection/import failures because `site_profiles` and the new CLI fields do not exist.

- [ ] **Step 3: Implement the registry domain**

Define immutable entries and a registry with the following public seam:

```python
@dataclass(frozen=True)
class SiteProfile:
    id: str
    label: str
    enabled: bool
    setup_status: str
    site_path: Path | None = None
    tag_map_path: Path | None = None
    pose_db: Path | None = None
    pose_mode: str | None = None
    surface_z_cm: float | None = None
    start_x_cm: int | None = None
    start_y_cm: int | None = None
    local_ip: str | None = None

class SiteProfileRegistry:
    def require(self, profile_id: str) -> SiteProfile:
        """Return one enabled, physically valid profile or raise StartupError."""

    def public(self, selected_id: str) -> dict:
        """Return only IDs, labels, enabled/status facts, and selection state."""

def load_site_profiles(path: Path) -> SiteProfileRegistry:
    """Parse a version-1 registry and resolve entry paths against path.parent."""

def apply_site_profile(config: SessionConfig, profile: SiteProfile) -> SessionConfig:
    """Return a replaced immutable SessionConfig containing physical fields."""
```

Use existing `load_site`, `load_tag_map`, `check_tag_map_matches_site`, `CoverageModel`, `envelope_from_site`, and `generate_options` for the same geometry checks used by Live planning. Raise `StartupError` with allowlisted codes such as `SITE_PROFILE_NOT_FOUND`, `SITE_PROFILE_DISABLED`, `SITE_PROFILE_INCOMPLETE`, and `INVALID_LIVE_SITE_OR_COVERAGE`.

- [ ] **Step 4: Add the portable default registry**

Create version `1` with `CanningSt` pointing to `../docs/wp5-flight1-site.json`, `../docs/wp5-flight3-map.json`, `../artifacts/canning-st/pose.sqlite`, pose `off`, surface `50`, start X `250`, start Y `190`; add disabled `Demo` with setup status `Venue calibration required`. Do not invent Demo geometry.

- [ ] **Step 5: Wire CLI and startup**

Add `site_profiles_path: Path | None` and `site_profile_id: str = 'CanningSt'` to `SessionConfig`. In Live mode, default `--site-profiles` to `config/mission-control-sites.v1.json`; resolve and apply the selected entry in `build_service` before constructing adapters. Rehearsal must ignore the registry. If explicit legacy physical flags are present without `--site-profiles`, validate them as one compatibility profile; an empty bare Live configuration must no longer serve.

- [ ] **Step 6: Verify GREEN and commit**

Run: `python -m pytest tests/ui/test_site_profiles.py tests/ui/test_bootstrap.py -q`

Expected: all selected tests pass and startup failure occurs before app/port binding.

Commit: `feat: require valid live site profiles`

### Task 2: Current-fact readiness and route discovery

**Files:**
- Modify: `src/sentinel/ui/readiness.py`
- Modify: `src/sentinel/ui/bootstrap.py`
- Modify: `src/sentinel/ui/service.py`
- Modify: `tests/ui/test_bootstrap.py`
- Modify: `tests/ui/test_api.py`

- [ ] **Step 1: Write failing readiness-refresh tests**

Add tests proving: no `LIVE_CONFIRM_REQUIRED`; no `LIVE_PAID_CALL_AUTH_REQUIRED` in Mission Control; missing provider env names are reported only when missing; provider profile/consent remain blockers; a discovered `192.168.10.2` route makes flight/emergency ready without `local_ip`; route disappearance reintroduces the blocker on the next snapshot; no hardware/provider object is constructed by polling.

```python
monkeypatch.setattr(readiness, 'discover_local_ipv4', lambda _: '192.168.10.2')
first = service.read_snapshot('mission')['readiness']
monkeypatch.setattr(readiness, 'discover_local_ipv4', lambda _: None)
second = service.read_snapshot('mission')['readiness']
assert ready(first, 'flight')
assert reason(second, 'flight') == 'LIVE_NETWORK_CONFIG_REQUIRED'
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/ui/test_bootstrap.py tests/ui/test_api.py -q`

Expected: assertions fail because readiness is cached, confirmation/paid-call startup flags are still checked, and no route discovery exists.

- [ ] **Step 3: Implement non-commanding IPv4 route discovery**

Implement `discover_local_ipv4(tello_ip: str) -> str | None` with a UDP socket route lookup (`connect` plus `getsockname`, no `send`) and validate that the result is a non-loopback IPv4 address. Prefer an explicit profile `local_ip`; discovery failure returns `None` and never connects to the drone protocol.

- [ ] **Step 4: Make readiness describe current facts**

Remove `LiveReadiness.command_template` and `_command_template`. `_flight` checks only the current network route; `_detection` checks provider selection/env/profile/consent but not `authorise_paid_calls`; `_emergency` uses the same discovered route. Keep one active reason per capability and never include secret values.

- [ ] **Step 5: Refresh on every snapshot**

In `MissionControlService.read_snapshot`, call `evaluate_live_readiness(self.config)` for Live sessions before projection, while preserving any dispatch-specific failure overlay until the underlying condition or a new dispatch changes it. Ensure this path performs only local file/database/env/network-interface reads.

- [ ] **Step 6: Verify GREEN and commit**

Run: `python -m pytest tests/ui/test_bootstrap.py tests/ui/test_api.py -q`

Expected: all focused tests pass, including appearance/disappearance without service restart.

Commit: `feat: refresh live readiness from current facts`

### Task 3: Idle-only atomic profile switching API

**Files:**
- Modify: `src/sentinel/ui/service.py`
- Modify: `src/sentinel/ui/api.py`
- Modify: `src/sentinel/ui/store.py`
- Modify: `tests/ui/test_api.py`
- Modify: `tests/ui/test_store.py`

- [ ] **Step 1: Write failing service/API tests**

Cover `GET /api/v1/site-profiles`, `POST /api/v1/site-profiles/select`, successful switch between two valid test profiles, disabled/invalid/refused responses, unchanged previous selection after failure, and refusal once any proposal, worker, runner, flight/evidence, terminal/recovery, or pending decision state exists. Spy on drone/emergency/provider constructors to prove switching is inert.

```python
response = client.post('/api/v1/site-profiles/select', json={'profile_id': 'LabB'})
assert response.status_code == 200
assert response.json()['selected_profile_id'] == 'LabB'
assert client.get('/api/v1/site-profiles').json()['selected_profile_id'] == 'LabB'
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/ui/test_api.py tests/ui/test_store.py -q`

Expected: profile routes return 404 and service methods are absent.

- [ ] **Step 3: Implement a single pristine/idle predicate**

Add a read-only store/service predicate that requires: active run status, no offers/decision commands, no events representing mission activity, inactive worker, no registered runner, no runtime state/evidence, and no terminal/recovery state. Reuse the predicate for both catalogue `switch_allowed` and mutation admission.

- [ ] **Step 4: Implement atomic switch**

Under a service configuration lock: validate the requested enabled profile; build local planning adapters before mutation; recheck pristine/idle; swap config/adapters/runtime planning composition and callbacks; close only replaced local pose resources; append `SITE_PROFILE_CHANGED`; refresh readiness. On any exception, keep the old config, adapters, runtime, profile ID, and persistent facts unchanged.

- [ ] **Step 5: Add dedicated endpoints**

Expose:

```python
@router.get('/site-profiles')
def site_profiles(): return service.read_site_profiles()

@router.post('/site-profiles/select')
def select_site_profile(command: dict):
    return result(service.select_site_profile(command))
```

Allowlist response fields: registry version, selected ID, switch legality/reason, and each profile's ID/label/enabled/setup status. Return typed `CONFLICT` for non-idle and typed `INVALID` for validation failures; never return resolved paths or private exception strings.

- [ ] **Step 6: Verify GREEN and commit**

Run: `python -m pytest tests/ui/test_api.py tests/ui/test_store.py -q`

Expected: all selected tests pass and the snapshot still validates as schema v3.

Commit: `feat: switch live site profiles while idle`

### Task 4: Approval-time flight and paid-call admission

**Files:**
- Modify: `src/sentinel/ui/live.py`
- Modify: `src/sentinel/ui/runtime.py`
- Modify: `src/sentinel/ui/mission_worker.py`
- Modify: `tests/ui/test_bootstrap.py`
- Modify: `tests/ui/test_mission_worker.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_live_consent_gates.py`

- [ ] **Step 1: Write failing dispatch-order tests**

Prove approving the exact immutable proposal is the sole Mission Control flight confirmation; reject/no proposal never constructs emergency transport, drone client, preflight, or detector supervisor; provider profile/consent admission happens before hardware/provider construction; approval then orders emergency transport, client, bounded no-takeoff preflight, supervisor creation/start, and flight. Verify standalone `live_detect`, survey, and cube paths still require explicit paid-call authorization.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/ui/test_bootstrap.py tests/ui/test_mission_worker.py tests/test_cli.py tests/test_live_consent_gates.py -q`

Expected: Mission Control dispatch remains blocked without startup flags or supervisor receives `authorised=False`.

- [ ] **Step 3: Bind dispatch authorization to approval**

Keep the existing immutable proposal identity/version check and `on_approval` waiter as the only path into `run_survey` flight execution. Add an assertion/regression seam at the Live flight boundary proving `fly()` is reached only after that waiter returns `True`; do not add any chat, voice, or general API field that can synthesize authorization.

- [ ] **Step 4: Authorize paid calls only inside approved Mission Control dispatch**

Construct `LiveDetectionSupervisor` with its `authorised` argument set to `True` only after current provider-profile/consent readiness passes and after real-flight approval. Remove Mission Control dependence on `SessionConfig.authorise_paid_calls`; do not alter standalone CLI parsers or gates.

- [ ] **Step 5: Verify GREEN and commit**

Run: `python -m pytest tests/ui/test_bootstrap.py tests/ui/test_mission_worker.py tests/test_cli.py tests/test_live_consent_gates.py -q`

Expected: focused suites pass and ordering spies show no operational action before approval.

Commit: `feat: admit live dispatch on proposal approval`

### Task 5: Browser profile selector and truthful readiness

**Files:**
- Modify: `examples/survey-console/http-client.mjs`
- Modify: `examples/survey-console/view-model.mjs`
- Modify: `examples/survey-console/mission-app.mjs`
- Modify: `examples/survey-console/approval.mjs`
- Modify: `examples/survey-console/console.css`
- Modify: `tests/survey_console/http-client.test.mjs`
- Modify: `tests/survey_console/status.test.mjs`
- Modify: `tests/survey_console/approval.test.mjs`

- [ ] **Step 1: Write failing browser tests**

Cover catalogue GET/switch POST encoding, disabled options, selector lock after activity, successful switch triggering immediate catalogue/snapshot refresh, failed switch preserving selection and showing the allowlisted reason, absence of the generic restart/full command text, and exact Live approval copy: `Approve & dispatch real flight`. Verify Rehearsal copy and voice prohibition remain unchanged.

- [ ] **Step 2: Run Node tests and verify RED**

Run: `node --test tests/survey_console/http-client.test.mjs tests/survey_console/status.test.mjs tests/survey_console/approval.test.mjs`

Expected: missing profile client methods/selector behavior and obsolete readiness copy assertions fail.

- [ ] **Step 3: Implement client and selector**

Add `read_site_profiles()` and `select_site_profile(profile_id)` to the HTTP client. Render a labelled `<select id="site-profile">` in Live only; use option text from public labels/status; disable unavailable options and the whole control when `switch_allowed` is false or a mutation is pending. After mutation, fetch catalogue and call the existing controller refresh.

- [ ] **Step 4: Render current blockers only**

Delete the generic restart paragraph and command block. Keep the per-capability reason code/remediation list and the existing ARIA linkage. Update approval copy so Live clearly says approval dispatches the real flight and begins bounded preflight; Rehearsal remains non-physical.

- [ ] **Step 5: Add compact responsive styling**

Place the profile control in the command/readiness area without changing the three-column projector hierarchy. At narrow widths it must occupy the available width, preserve focus visibility, and avoid horizontal overflow. Respect the existing reduced-motion rules.

- [ ] **Step 6: Verify GREEN and commit**

Run: `node --test tests/survey_console/*.test.mjs`

Expected: all browser unit tests pass.

Commit: `feat: add live site profile selector`

### Task 6: Documentation, real-host acceptance, and full regression

**Files:**
- Modify: `README.md`
- Modify: `examples/survey-console/README.md`
- Modify: `docs/handoffs/2026-09-21-mission-control.md`
- Modify: `scripts/ui_assets/verify_survey_console.cjs` if selector assertions require it

- [ ] **Step 1: Update operator documentation**

Document the simple launch:

```powershell
sentinel mission-control --mode live --site-profile CanningSt --data-dir artifacts/local-live
```

Explain that startup requires only a valid physical profile; readiness updates on polling; profile switching is idle-only; Live approval confirms and dispatches real flight/provider calls; standalone paid-call gates remain; `Demo` intentionally stays disabled until calibrated. Include how to add another registry entry without changing code.

- [ ] **Step 2: Run complete automated verification**

Run:

```powershell
python -m pytest -q
node --test tests/survey_console/*.test.mjs
python scripts/ui_assets/validate_assets.py
```

Expected: zero failures; only previously documented skips/warnings.

- [ ] **Step 3: Run real-host browser acceptance without dispatch**

Start Live Mission Control using `CanningSt` and a fresh data directory. Run `node scripts/ui_assets/verify_survey_console.cjs http://127.0.0.1:<port>`, then inspect 1440x900, 1920x1080, and narrow layouts. Verify the selector, disabled Demo entry, no overflow, keyboard access, truthful readiness refresh, and that no drone/provider construction occurs merely from startup, switching, or polling. Do not approve or dispatch a physical flight.

- [ ] **Step 4: Review requirements line by line**

Check every design-spec bullet against code/tests: startup physical gate, extensible registry, idle-only atomic switching, schema-v3 compatibility, dynamic route/env/profile/consent readiness, approval-time confirmation, Mission-Control-only implicit paid calls, unchanged standalone gates, Rehearsal compatibility, and voice inability to approve/emergency.

- [ ] **Step 5: Request final spec and quality review, fix findings, re-run verification, and commit**

Dispatch independent reviewers with the design spec and full commit range. Fix every Critical/Important finding through a failing regression test first, request re-review, then repeat the full commands above.

Commit: `docs: document live site profile workflow`
