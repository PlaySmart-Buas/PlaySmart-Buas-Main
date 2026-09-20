# PlaySmart capture rig — setup and test guide (League of Legends)

Added 2026-09 (iteration 5). How to set up a capture PC, and the test sequence that
proves every stream is real before anyone plays a match that matters. Work through it
in order; each test has a pass condition.

Sections: 1 set up a PC · 2 pre-flight · 3 OBS · 4 tests T1–T8 · 5 results table ·
6 things that look wrong but are not · 7 real blockers · 8 troubleshooting.

Time budget: a new PC about 45 minutes (mostly downloads and the Tobii calibration); the
test sequence about 90 minutes if nothing goes wrong.

## 1. Setting up a capture PC

### 1.1 Windows itself (once per PC)

- **Local admin** for the installs; the capture runs as a normal user afterwards.
- **Windows 11 + Vanguard**: League requires Riot Vanguard, which on Windows 11 requires
  **TPM 2.0 and Secure Boot enabled in the BIOS**. On a freshly imaged PC this is the
  thing most likely to eat an hour. Check first: `Get-Tpm` and `Confirm-SecureBootUEFI`
  in an admin PowerShell.
- **Display**: 1920×1080, **100 % scaling** (Settings → System → Display). Anything else
  puts gaze coordinates, the input log and the OBS canvas on different grids.
- **Power**: never sleep, screen never off while plugged in. A sleeping capture PC is a
  44-byte WAV.
- **Privacy → Camera** and **Privacy → Microphone**: *Let desktop apps access* must be
  **on**, or OpenFace and `sounddevice` open nothing and say very little about why.
- **Bluetooth on** for the Nuanic ring, but do **not** pair it in Windows settings —
  `nuanic_eda.py` finds it by service UUID and connects itself; a Windows-level pairing
  can hold the connection and hide the ring from `bleak`.
- Time sync on (*Set time automatically*). Every stream is stamped with this clock.

### 1.2 One script for the rest

```powershell
git clone https://github.com/PlaySmart-Buas/PlaySmart-Buas-Main.git C:\League_work\PlaySmart-Buas-Main
cd C:\League_work\PlaySmart-Buas-Main
git checkout feature/league-collection          # until it is merged
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

`setup.ps1` installs Git, Python 3.10, OBS Studio, the VC++ 2017 runtime and the GitHub
CLI through winget; installs Poetry and builds the environment on 3.10 (`tobii-research`
ships cp310 wheels only); downloads OpenFace 2.2.0 and its CEN models; creates `.env`
from `.env.example` and asks for the OBS and SFTP passwords. It is idempotent — re-run it
after a partial failure. `-SkipInstalls`, `-SkipOpenFace`, `-WithTranscription`,
`-WithVision` are documented at the top of the script.

Only the capture dependencies are installed (43 packages, ~300 MB). Whisper, Torch and
YOLO are optional groups; nothing `key_listener.py` starts imports them.

### 1.3 The two things the script cannot do

- **Tobii Pro Eye Tracker Manager** (link printed by the script): installs the driver,
  updates firmware, and is where you **calibrate the player before every session**.
- **League of Legends**: install, log in with the account that will play, open Practice
  Tool once so first-run patching happens before the lab session.

Then OBS (section 3), then `poetry run python src\preflight.py`.

### 1.4 An already-set-up PC

```powershell
cd C:\League_work\PlaySmart-Buas-Main
git pull
poetry install
poetry run python src\preflight.py
```

## 2. Pre-flight

```powershell
poetry run python src\preflight.py            # everything, ~15 s
poetry run python src\preflight.py --no-ble   # skip the 8 s ring scan
```

One line per item, `ok` / `warn` / `FAIL`; exit code 1 on any `FAIL`. It checks: Python
3.10, every capture import, `.env` and the two passwords, disk space, resolution and
scaling, the Tobii tracker (model, serial, rate), OpenFace binary and models, default
microphone and a one-second level sample, the EDA ring advertising, OBS (version,
websocket ≥ 5.1, canvas, whether the current scene has a game capture, recording
folder), whether the League API is answering, and whether the SFTP server is reachable.
`main.bat` runs it before every capture.

Not checked, look yourself: which webcam is device 0 (Windows Camera app), and where the
overlay window ("Gaze and Emotion Overlay", borderless, full-size on monitor 0) appears.
If it sits on top of the game, write that down; Game Capture of the League window keeps
it out of the video regardless.

## 3. OBS

Once per PC, five minutes: `obs settings\readme.md`, top section. In short: import
`obs settings\PlaySmart_League.json` (one Game Capture of `League of Legends.exe`, 1920×1080),
enable the WebSocket server and put its password in `.env`, set MKV with auto-remux off,
recording path `C:\Users\<user>\Videos`.

**Smoke test, no Python.** With Practice Tool open: OBS preview shows the game. Start
Recording, wait 10 s, Stop Recording, open the file in `Videos` — game visible, not black,
not the overlay.

**Smoke test, obsws.** OBS open, no game needed:

```powershell
poetry run python src\preflight.py --no-ble --no-mic
```

The `obs`, `obs video`, `obs scene` and `obs recording dir` lines must all be `ok`.

**Smoke test, obs_recorder.** From the repo root:

```powershell
poetry run python -c "import sys, time; sys.path.insert(0, 'src'); import obs_recorder as o, session as s; sid = s.new_session_id(); h = o.start(sid); time.sleep(8); print(o.stop(sid, h))"
```

Pass: `started video (frame zero at 17…)` — a number, not the *unaligned* warning — then
`video saved: <sid>_video.mkv (N MB)`, and `data\video\<sid>_video.json` has a non-null
`t0_unix_ms`. Every failure path prints what is wrong (`obs_recorder.py` docstring).

## 4. Test sequence

Switches live in `.env` (see `.env.example`): `PLAYSMART_SKIP=eda` if there is no ring,
`PLAYSMART_OPENFACE=0` if OpenFace is not installed. Never set `PLAYSMART_MOCK_GAZE`.

### T1 — orchestration, no hardware (2 min)

```powershell
poetry run python src\key_listener.py --dry-run --once
```

Pass: five lines `… saved and exited`, `Session output:` shows five `ok`, and
`data\stub-*\` holds five files sharing one session id.

### T2 — game state alone (5 min)

```powershell
poetry run python src\liveclient_recorder.py --once
```

Start a Practice Tool game, play two minutes, get one kill on a dummy, end the game.

Pass: `poetry run python src\check_session.py` prints `ok clock mapping`, an events line
with `ChampionKill`, and `Session looks usable.` — `WITH BOTS` is expected in Practice Tool.

### T3 — OBS alone

The three smoke tests in section 3. Do not go on until the `obs_recorder` one passes.

### T4 — full stack (20 min)

```powershell
main.bat
```

(or `poetry run python src\key_listener.py --once`). Press **F7**. Check that OBS is open
and its preview shows League *before* starting the game. Start Practice Tool as yourself and
play at least **five minutes**, including all of:

- one kill and one death (spawn a dummy / enable bots, let it kill you once);
- a deliberate glance at the minimap every ~30 s — that is the first metric;
- one press of the **+30 s** button — the clock check must report it as a *warn*, not *BAD*;
- **end the game from the menu**, not F12.

Watch the console for, in order: `started` ×5, `started video (frame zero at …)`,
`Game over.`, `video saved: <sid>_video.mkv`, `saved and exited` ×5 (or `exited cleanly
but wrote nothing` for a stream you skipped — that is the honest message, not an error),
`Session output:` all `ok` including `video`, then the upload. `/data/gamestate/` upload
failing is expected until the directory exists on the server.

Then `poetry run python src\check_session.py`.

Pass: `ok clock mapping` with a `warn` line for the +30 s skip; gaze `> 85 %` valid,
`> 95 %` on screen, minimap share in the 3–30 % band; input APM 100–300; every stream
`covers > 95 %` of the game window; `Session looks usable.`

Expected sizes for a 5-minute game: gaze ~1.5–4 MB (tracker rate × 300 s, 100 % valid);
audio ≈ 5.3 MB per minute (44.1 kHz mono 16-bit) so ~26 MB — **anything near 44 bytes
is the old bug back**; gamestate ~600 rows; input a few thousand rows; emotion CSV, if
OpenFace ran, ~95 % `Neutral` (known classifier limitation, not a capture failure); video
tens to a few hundred MB depending on encoder bitrate.

### T5 — video timing acceptance test (5 min)

```powershell
poetry run python src\check_video_sync.py
```

It prints three positions in the video file and the game clock the HUD should show at
each. Open `data\video\<sid>_video.mkv` in VLC, seek to each position, read the clock in
the top-right HUD.

Pass: all three within **1 s** (the HUD has one-second resolution). A constant offset of
several seconds means `t0` is wrong — copy the `frame zero at` line and the first
`unix_time_ms` from the gamestate CSV into the results table. An offset that grows through
the game means dropped frames or a VFR recording: check the encoder / fps settings.

`check_video_sync.py <sid> --at 4:37` checks one position of your choosing.

### T6 — F12 abort (5 min)

Run the capture again, F7, start a game, press **F12** after about a minute.

Pass: `Aborted.` then the same `video saved` / `saved and exited` sequence; OBS's own
window shows it is no longer recording; `check_session.py` is `ok` on a one-minute game.

### T7 — degrade path on real hardware (3 min)

Close OBS entirely. Run the capture, F7, play 30 s, F12.

Pass: `!! could not reach OBS on 127.0.0.1:4455 … no video this session.` at start; every
other stream saves; `Session output:` does not list `video` at all (it is only expected
when OBS actually started). Reopen OBS afterwards.

### T8 — the Tobii double-subscription question (5 min, optional)

`eye_tracking_script.py` and `Emotion_gaze_visualization.py` both subscribe to the same
tracker. Whether that costs samples has never been measured: set `PLAYSMART_SKIP=emotion`
in `.env`, run a 2-minute game, note the gaze Hz from `check_session.py`, remove the
setting. Same rate (±5 %) as T4 clears the suspicion; a lower rate with the overlay
running is a finding.

## 5. Results table

Paste it into the pull request when done.

| test | sid | result | note |
|---|---|---|---|
| T1 dry-run | | | |
| T2 gamestate alone | | | |
| T3 OBS smoke (preflight / obs_recorder) | | | OBS version, websocket version |
| T4 full stack | | | streams present, gaze Hz, gaze valid %, audio MB |
| T5 video sync | | | offsets at the three checkpoints |
| T6 F12 abort | | | |
| T7 OBS closed | | | |
| T8 overlay off | | | gaze Hz with vs without overlay |

Also record: OBS version, encoder, recording format, tracker model and rate, OpenFace
present yes/no, ring paired yes/no.

## 6. Things that will look wrong but are not

- `/data/gamestate/` upload fails — the server directory does not exist yet.
- `WITH BOTS` and `PRACTICETOOL` in the report — Practice Tool has no Riot match id, so no
  Match-V5 timeline will ever attach to these sessions; fine for testing the pipeline.
- Emotion ~95 % `Neutral` — `map_emotion()` requires several action units to fire at once
  and returns Neutral otherwise; documented limitation.
- `unix_time_ms` on the events stream is stamped when the poll *saw* the event; use
  `game_time_s` on that stream.
- `exited cleanly but wrote nothing` for a skipped or absent sensor — the report working.
- `warn` on the clock mapping after the +30 s button — the tolerant clock check working.

## 7. Real blockers

- **Consent.** No participant other than yourself until informed consent for gaze, face,
  voice and physiology is in place. EU AI Act Art 5(1)(f) restricts emotion inference from
  biometric data in education settings; the research exemption needs a decision from the
  DPO, not from us. Test sessions are on yourself, and they still contain your face
  (OpenFace `output/`), voice and gaze — `data/` and `output/` stay out of git.
- **`data/json/ign_mapping.json`** maps Riot IDs to participant numbers. It is the
  re-identification key: it stays on the capture PC, is never uploaded, never committed.
- The SFTP password must be rotated on the server; the old one is in this repository's
  git history forever.

## 8. When something fails

| symptom | first thing to check |
|---|---|
| `could not reach OBS` with OBS open | WebSocket server not enabled, port ≠ 4455, or the password in `.env` is wrong |
| `OBS never reported a recording duration` | OBS < 29, or recording did not actually start (no source? encoder error in the OBS log) |
| `OBS did not report an output path` | OBS 28 (websocket 5.0): upgrade, or rely on the `claim_video` fallback |
| `gaze exited immediately` | `find_all_eyetrackers()` empty — Tobii service, USB, or Eye Tracker Manager holding the device |
| overlay dies at second four | OpenFace path wrong, or models not downloaded; it now continues as gaze-only and says so |
| OpenFace starts and exits at once | `download_models.ps1` never ran (no `model\patch_experts\cen_*.dat`), or VC++ 2017 x64 missing |
| webcam / mic open nothing | Windows Privacy → Camera / Microphone → desktop apps blocked |
| ring never found | paired at Windows level (unpair it), Bluetooth off, or start again — the 2 s start delay was not enough |
| audio file tiny | the recorder was killed instead of stopped — `stop_recorders` timeout messages in the console say which one hung |
| `check_session` BAD clock mapping | the game restarted under one session id, or > 30 s without a poll (client stalled) |
| video is black | OBS source is the overlay or "any fullscreen app"; re-import `PlaySmart_League.json` |
| `poetry install` fails on `tobii-research` | Poetry is not on 3.10: `poetry env use (py -3.10 -c "import sys; print(sys.executable)")` |
