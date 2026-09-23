# Live Site Profiles and Runtime Readiness Design

## Goal

Make Live Mission Control start only with a valid physical site/map configuration, while allowing an idle operator to switch between named configurations without restarting the HTTP host. Readiness must describe current facts rather than repeat a generic launch command.

The initial registry contains `CanningSt`, the measured development setup, and a disabled `Demo` entry that can be completed during venue calibration. The registry format supports additional entries without code changes.

## Boundaries

Physical configuration is the only startup gate. The selected enabled profile must resolve to a valid site document, matching tag map, pose database location, pose mode, surface height, and any start coordinates required by that pose mode. Mission Control exits before binding its HTTP port when the selected profile is absent, disabled, incomplete, or geometrically invalid.

Provider profile, filming consent, drone connection, emergency transport, and provider availability are not UI-startup gates. They remain capability-admission checks at dispatch. Rehearsal behavior is unchanged.

## Site profile registry

Mission Control reads a versioned JSON registry supplied by `--site-profiles`, with a repository default for the demo laptop. `--site-profile` selects an entry and defaults to `CanningSt` for the current development workflow. Paths in an entry are resolved relative to the registry file so the configuration remains portable.

Each enabled entry contains:

- stable ID and operator label;
- site and tag-map paths;
- pose database path and pose mode;
- surface height;
- start X/Y coordinates when pose mode is `off`;
- optional local IPv4 override for unusual network layouts.

Disabled draft entries require only an ID, label, and setup status. They are listed in the UI as unavailable and cannot be selected. Completing `Demo`, or adding another venue, is a data change followed by registry validation rather than a code change.

## Runtime switching

The browser reads an allowlisted profile catalogue and selected profile from dedicated `/api/v1` profile endpoints. Selecting an enabled profile asks the backend to validate and apply it atomically. A switch is legal only while the mission is pristine and idle: there is no proposal, worker, active runner, flight, evidence, terminal recovery state, or pending decision. Once mission work begins, the selector locks until a fresh idle session is created.

Applying a profile reconstructs only the local planning composition, publishes a configuration-change event, advances the public snapshot, and re-evaluates readiness. It does not connect to the drone, initialize emergency transport, call a provider, record consent, or delete prior data. A failed switch leaves the previous profile active and returns an allowlisted validation reason.

The existing schema-v3 mission snapshot remains compatible. Profile catalogue and mutation use separate responses rather than adding fields to the closed snapshot contract.

## Live readiness refresh

Readiness is recalculated from current external facts whenever the browser reads a snapshot and immediately after a profile switch or dispatch admission result. The browser's existing polling therefore clears or changes warnings without restarting Mission Control.

Readiness checks report only the active blocker for each capability. The generic restart sentence and full command template are not rendered. Provider environment names may appear only when genuinely absent; detected values are never returned. Provider profile and consent changes become visible on the next poll.

Network readiness discovers the IPv4 interface that routes to the configured Tello address. The currently connected development interface (`192.168.10.2` for `192.168.10.1`) therefore satisfies configuration readiness without a `--local-ip` restart. Discovery sends no drone command. An explicit profile override remains available when route discovery is ambiguous.

## Dispatch consent and paid calls

Approving the current immutable proposal is the operator's flight confirmation. The approval control must state that approval dispatches the real flight. Mission Control no longer requires startup `--confirm`; it performs drone construction, emergency-transport initialization, and the bounded no-takeoff preflight only after that approval.

Paid detector calls are implicit in Live Mission Control after proposal approval. Mission Control passes authorization to the supervised detector only at dispatch, after provider-profile and filming-consent checks pass. Standalone `live_detect`, cube-trial, and survey CLI commands retain their explicit `--i-authorise-paid-calls` gates.

Voice remains unable to approve or reject proposals. It cannot supply flight confirmation or authorize paid calls.

## Failure handling

- Invalid selected physical profile: process exits before serving the UI.
- Invalid idle profile switch: previous profile remains active; no adapters or persistent mission facts change.
- Network absent or ambiguous: UI stays available and reports a current network blocker; it clears after the route appears.
- Provider profile or consent absent: planning and proposal review remain available, but approved dispatch fails before hardware/provider activity and the current reason is projected.
- Connection or no-takeoff preflight failure: no takeoff occurs; readiness reports the dispatch failure.
- Profile change attempted after mission activity: request is refused without altering the active session.

## Testing

Tests are written before implementation and cover:

- startup refusal for a missing, disabled, incomplete, or mismatched selected physical profile;
- successful `CanningSt` startup and disabled `Demo` catalogue projection;
- additional registry entries without code changes;
- atomic idle switching and refusal after proposal/worker/runner activity;
- no drone, emergency, or provider construction during startup or switching;
- network appearance/disappearance updating readiness without restart;
- exact provider-environment errors only when names are absent;
- removal of the generic restart command from the browser;
- proposal approval serving as flight confirmation and invoking preflight only after approval;
- implicit paid-call authorization only in Mission Control, with standalone CLI gates unchanged;
- provider-profile and consent refusal before hardware/provider activity;
- schema-v3 compatibility, Rehearsal behavior, voice prohibitions, and full Python/Node/browser regression suites.

Physical flight and venue calibration remain hardware validation tasks and are not inferred from fake-client tests.
