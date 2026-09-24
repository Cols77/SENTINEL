# Room agent: morning Live flight checklist (CanningSt)

Go through this in order, and do not skip a line. Everything runs in PowerShell at this checkout's root.
The full background is in [the live runbook](room-agent-live-runbook.md).

## 1. Aircraft and room

- [ ] **Battery at least 20 %**. Check it in the Tello app or on the L0 line `battery=<n>%`. This is the landing reserve floor, not a promise of takeoff: the calibrated guard refuses takeoff below about 45 % and may require 55-60 % before an observation.
- [ ] **Tello on the CanningSt pad at (250, 190) cm**, nose toward the tag stands, with both tags in its camera view.
- [ ] **Cube clear.** Measure the free distance from the pad **with the tape** in every direction the aircraft moves:
  - left and right: at least 90 cm;
  - back: at least 30 cm;
  - forward: at least 110 cm;
  - up: 2 m.

  Nothing and nobody may be inside the cube.
- [ ] Everyone who may appear in frame today has agreed to be filmed.

## 2. Laptop

- [ ] **One adapter on the Tello Wi-Fi** (`TELLO-...`), and **internet on a second adapter** (Ethernet or a USB tether) that holds the default route.
- [ ] Note the laptop's address on the Tello Wi-Fi (usually `192.168.10.2`, from `ipconfig`).
- [ ] `OPENROUTER_API_KEY` and `GEMINI_OPENROUTER_MODEL` are set in the environment or in `.env`.

## 3. Prepare and launch

```powershell
$env:PYTHONPATH = "src;."
python scripts/room_live_prepare.py --run-root runs --local-ip 192.168.10.2 --consent-confirmed
```

Pass `--consent-confirmed` only when everyone in frame has agreed: it records today's CanningSt filming consent in your name, and the script prints a notice saying so. Without the flag, the script needs a consent record already made today.

The script makes no network or drone contact. It does the following:

- creates or reuses `runs/registry.json`;
- issues the `count_return` TRIAL grant, or reuses the current run's grant;
- previews the manifest;
- writes both site assurances, valid for **30 minutes**;
- writes `runs/<RUN>.pack.json`, plus the same content to `runs/live.pack.json`.

It then prints the pack, the assurance expiry in local time, the L0 command and the launch command:

```powershell
$env:PYTHONPATH="src;."
python -m sentinel.ui --mode live --site-profile CanningSt --data-dir artifacts/room-live --room-live-config runs/live.pack.json --i-authorise-paid-calls
```

- [ ] **Note the assurance expiry time.** Everything up to landing must happen before it.

## 4. Motors-off check (L0), then the takeoff check

- [ ] Run the L0 command the script printed (`room_session.py live ... --check-connectivity-only`). Expect `CONNECTIVITY_READY` and a `battery=` line. It uses the pack's own manifest, so the run stays usable for the flight.
- [ ] Run `python scripts/room_takeoff_check.py --execute --local-ip 192.168.10.2`. It takes off, hovers about 3 s and lands. `E` stops the motors.
- [ ] Put the Tello back on the pad mark and check that both tags are still in view.

Mission Control's Live run repeats the same motors-off preflight before it takes off.

## 5. Count flight (Mission Control)

- [ ] Open Mission Control and go to **Room agent (Jev adaptive)**, then **Live**.
- [ ] Type the request **"count the phones and computers in the room"**. The manifest does not bind the request text: the typed request is the one flown, under the `count_return` grant.
- [ ] Press **Propose run**, then **Approve**. Nothing is built before you approve.
- [ ] **Watch the re-anchor log**: the localization state and fix age, the tag bursts, the yaw rotations, and each accepted re-anchor.
- [ ] Keep a hand on the stops:
  - **Land** lands through the room executor.
  - **`E`** in the Mission Control terminal stops the motors.
  - **EMERGENCY STOP** stops the motors and latches the session: restart Mission Control before flying again.
- [ ] Check the result: the answer, the return home, and the landing.

## 6. Search flight

- [ ] Check the battery again. Fit a charged pack if it is under 20 % or the calibrated reserve cannot admit the next flight. Put the Tello back on the pad and re-check the cube.
- [ ] **Re-run the prepare script** (no `--consent-confirmed` is needed the same day). A run that has flown is never reused, so this gives a new run, a new grant and fresh 30-minute assurances. Do the same whenever the 30 minutes have run out. Mission Control reads `runs/live.pack.json` again at each approval, so it does not need a restart.
- [ ] In Mission Control: **Live**, then **"find the phone"**, then **Propose run** and **Approve**. Watch it the same way as the count flight.

## Common refusals

| Message (panel or terminal) | Meaning | What to do |
|---|---|---|
| `refused: no filming consent recorded for CanningSt on <date>` (prepare) | There is no consent record for today. | Re-run prepare with `--consent-confirmed`, but only when everyone in frame agreed. |
| `PAID_CALLS_NOT_AUTHORISED` | Mission Control was started without `--i-authorise-paid-calls`. | Restart it with the printed launch command. |
| `ROOM_LIVE_CONFIG_REQUIRED` / Live greyed out | Mission Control was started without `--room-live-config`. | Restart it with the printed launch command. |
| `SESSION_EMERGENCY_LATCHED` | EMERGENCY STOP was used. | Restart Mission Control. |
| `the pack's run <RUN> already flew; re-run scripts/room_live_prepare.py` | The pack's run has finished. | Re-run the prepare script, then approve again. |
| `site assurance for trigger ... is missing, expired or not uniquely current` | The 30 minutes have run out. | Re-check the floor, re-run prepare, then approve again. |
| `acceptance admission requires exactly one matching TRIAL grant ...` | The grant expired (after 6 h), or the files changed. | Re-run prepare with `--new-run`. |
| `connectivity preflight not ready (... drone_error=NoTelloAdapter ...)` | No adapter is on the Tello Wi-Fi, or the Tello adapter owns the default route. | Join the Tello Wi-Fi, keep internet on the other adapter, check `--local-ip`. |
| `... drone_error=AmbiguousAdapter ...` | Several adapters reach a Tello. | Pass `--local-ip` (to prepare, or type it in the panel). |
| `... drone_error=TimeoutError ...` | The Tello is off, asleep or out of range. | Power-cycle it, rejoin its Wi-Fi, approve again. |
| `... provider_error=...OPENROUTER_API_KEY...` / `HTTP 401/402` | The key is missing, invalid or out of credit. | Fix the key or `.env`, then restart Mission Control. |
| `connectivity preflight expired before takeoff` | Goal compilation outlasted the 90 s preflight age. | Approve again. |
| `takeoff refused: start blocked: LOCALIZATION_LOST` | The pad does not see two tags. | Re-seat the Tello on the mark with the tags in view. Nothing flew. |
| Takeoff refused on battery / the aircraft returns early | The battery is below the reserve (about 45 % for takeoff, 55-60 % per region). | Fit a fresh battery. |

More refusals, with their causes, are listed in the runbook's "When L0 refuses" and "When L3 refuses before takeoff" tables.
