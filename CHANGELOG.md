# Changelog

All changes to the capture toolkit, with the reason for each. Apache 2.0 asks that
modified files say so; every file touched carries a `Modified 2026-09` / `Added 2026-09`
notice at the top, and this file is the index.

## Iteration 5 — 2026-09 — League of Legends port of the capture rig

Team: Hristiyan Georgiev (BUas ADS&AI year 3) with two teammates; mentor Bram Heijligers;
infrastructure Uther Tlas. Branch `feature/league-collection`, based on `Dev`.

### Why

An audit of the 2024–25 recordings (four capture PCs, 19.6 GB, Nov 2024 – Nov 2025)
found 364 minutes of video with 171 minutes of content, several 44-byte WAV files, gaze
files 2.6 hours away from the session named on them, and eleven identical merged CSVs on
one machine. Each traced to a mechanism, not to bad luck: recorders were killed with
`terminate()` before they wrote; files were paired afterwards by "newest file in the
folder"; video was started by hand and captured the wrong window. The changes below
remove those mechanisms rather than patch around them.

### Capture — what a session now is

| file | change | reason |
|---|---|---|
| `src/session.py` **new** | One session id per game, passed to every recorder through the environment; every stream names its file `<sid>_<stream>.<ext>` at creation. A stop *signal* (`stop_requested()`) the recorders poll. Loads `.env`. | Ends the newest-file pairing that attached one session's data to another. Gives the buffering recorders a way to be *asked* to stop. |
| `src/key_listener.py` rewritten | Capture brackets the game, not the operator: F7 arms, the recorders start when League's local API appears and stop when it disappears. Stop = request → wait per recorder → terminate only on timeout. End-of-session report per stream; a clean exit with an empty file is reported as such. `PLAYSMART_SKIP` drops absent sensors from both the start list and the expectations. | `terminate()` two seconds after F12 is what truncated gaze, EDA and audio. With automatic stopping there is no F12 to hook, so graceful stop was a prerequisite. A report line that is always red gets ignored. |
| `src/liveclient_recorder.py` **new** | Polls League's Live Client Data API (`127.0.0.1:2999`) at 2 Hz: game state with both `unix_time_ms` and `game_time_s` per row, events deduplicated on `EventID`, `<sid>_meta.json` with Riot ID, champion, mode, map, bots. | The clock mapping is what lets a gaze sample be placed at "14:32 into the game". No key, no rate limit; answers in Practice Tool and customs, which Match-V5 does not. Replaces the free-text game dropdown that produced nine spellings of two game names. |
| `src/obs_recorder.py` **new** | Starts/stops OBS over obs-websocket 5 on the same signal as every other stream. Frame zero located from `GetRecordStatus.outputDuration` (median of bracketed samples, zero-duration samples discarded); written to `<sid>_video.json`. File claimed by the path `StopRecord` reports and moved to `data/video/<sid>_video.<ext>`. Degrades to "no video" on every failure path without touching the other streams. `PLAYSMART_OBS=0` disables. | Video was the last stream started by hand and named by OBS. A file's mtime says when it was written, not when frame zero was captured; without `t0` the video cannot be placed on the game clock. |
| `src/eye_tracking_script.py` | Session-named file, stop signal, save in `finally`. A missing tracker refuses to record; `PLAYSMART_MOCK_GAZE=1` writes rows flagged `gaze_valid=False`. Two commented-out legacy copies (295 lines) removed. | The old fallback wrote a whole session of random "gaze" indistinguishable from real data. |
| `src/Emotion_gaze_visualization.py` | Session-named file, stop signal. Emotion readout drawn at 1080p (was hardcoded `y=1400`, off-screen). Reads only the tail of OpenFace's growing CSV (was re-parsing the whole file twice a second). Survives a missing OpenFace binary as a gaze-only overlay; `PLAYSMART_OPENFACE=0` makes that deliberate. | The overlay crashing took the gaze dot — the operator's only live confirmation the tracker works — down with it. |
| `src/keyboard_recording.py`, `src/microphone_recording.py`, `src/nuanic_eda.py` | Session-named files, stop signal. Whisper transcription off unless `PLAYSMART_TRANSCRIBE=1`. | The audio `SoundFile` context must close to finalise the WAV header — the 44-byte files are exactly an unfinalised header. Whisper ran inline right before the kill and never completed. |
| `src/pop_up_screen.py` rewritten | Collects the files carrying this session's id and uploads them; labels come from `<sid>_meta.json`. Participant pseudonym minted per Riot ID in `data/json/ign_mapping.json`, which stays local. Video: uploads what `obs_recorder` filed; the time-based claim from `~/Videos` is only the fallback. | No more guessing which files belong together, no more free-text game names. **Deliberate divergence from `Dev`:** `Dev`'s version uploads `ign_mapping.json` to the server; this version does not, because that file is the only re-identification key and must not travel with the biometric data. To be confirmed with Bram. |

### Quality control — new

| file | what it answers |
|---|---|
| `src/check_session.py` | Is this session usable? Clock mapping (tolerant of Practice Tool's +30 s skips; fails only on a clock running backwards or a > 30 s blind gap), per-stream coverage of the game window, gaze validity and on-screen share, minimap share, emotion label distribution, APM. Exit 1 on problems. |
| `src/check_video_sync.py` | Does the video sit on the game clock? Prints video positions and the HUD clock each should show, for checking by eye. |
| `src/preflight.py` | Is everything the rig needs there, before F7? Python, packages, `.env`, disk, display, tracker, OpenFace + models, mic, ring, OBS (version, canvas, scene, recording dir), League API, SFTP reachability. |
| `src/_stub_recorder.py` | Stand-in recorder for `key_listener.py --dry-run`: buffers and writes only when asked to stop, so a kill instead of a wait shows up as a missing file. |

### Setup — from an afternoon to one command

| file | change |
|---|---|
| `setup.ps1` **new** | winget installs (Git, Python 3.10, OBS, VC++ 2017, gh), Poetry on 3.10, `poetry install`, OpenFace 2.2.0 + CEN models, `.env` from `.env.example`. Idempotent. |
| `pyproject.toml` | Python pinned `>=3.10,<3.11` (`tobii-research` is cp310-only). Dependencies split: the main group is exactly what the capture imports (43 packages, ~300 MB); Torch/Whisper (`transcription`), OpenCV/YOLO (`vision`) and nine packages nothing imports (`legacy`) are optional groups. `obsws-python` added. License field corrected to Apache-2.0 to match `LICENSE`. |
| `poetry.lock` | Regenerated against the groups (on Linux; the lock is platform-independent). |
| `main.bat` | Runs `preflight.py`, then the orchestrator. |
| `.env.example` **new**, `.gitignore` | Secrets and switches in `.env`. `.gitignore` now covers `data/`, `output/`, media files, `.env`, caches. |
| `obs settings/PlaySmart_League.json` **new**, `obs settings/readme.md` | A 1920×1080 scene collection with one Game Capture of `League of Legends.exe`. The League setup (websocket, video, recording) is the readme's top section; the iteration-4 overlay guide is kept below it. |
| `Toolkit setup.md`, `TESTING.md` **new**, `README.md` | Setup rewritten around `setup.ps1`; test sequence T1–T8 with pass conditions; README points here. |

### Security and privacy

- **`server/python_app/sftp_upload.py`**: the SFTP password was a literal in this public
  repository. It now comes from `PLAYSMART_SFTP_PASSWORD` (`.env`), and the uploader
  keeps files local with a clear message when it is unset. The old password is in git
  history forever and must be rotated on the server.
- `data/json/ign_mapping.json` and `date_game_count.json` were tracked despite `/data`
  being ignored (force-added). Untracked; `ign_mapping.json` is the re-identification key.
- Three `.pyc` files were tracked. Untracked.
- The scene collection deliberately has no Discord process-audio capture: recording other
  people's voices needs their consent.

### Verified

- 10 Sep 2026, home PC, Practice Tool: clock slope 0.999986, drift 3 ms over 101 s,
  events captured retroactively from the cumulative payload.
- 14 Sep 2026, lab PC_1, Practice Tool: audio 9.71 MB (was 44 bytes), gaze 5,804 samples
  at 56.8 Hz 100 % valid, input 980 events, game state 161 rows, 11 events; a dead
  recorder correctly reported.
- 20 Sep 2026, no hardware: all files byte-compile and pass `ruff check .` (the CI);
  `key_listener.py --dry-run --once` passes; `obs_recorder.py` against a mock OBS:
  `t0` within 15 ms, right file claimed with a newer decoy present, all four failure
  paths degrade cleanly; `poetry lock` + `poetry install` of the main group on 3.10 in
  23 s + 5 s.
- **21 Sep 2026, HIVE pc03, set up from a bare Windows install with `setup.ps1`:** first
  full-stack capture — session `20260921-153638-ae18ce`, a real CLASSIC game on Summoner's
  Rift (~18 min). Gaze 64,022 samples at 60 Hz, 100 % valid (Tobii Pro Spark); audio 104.7 MB;
  input, game state, events and meta; **video 57.5 MB started, stopped and filed by
  `obs_recorder` on the session id**. Upload correctly withheld with no SFTP password.
  Emotion wrote nothing despite OpenFace being present (undiagnosed); EDA absent (no ring).
- **Not yet verified:** `check_video_sync` (T5) on that session, EDA end to end, the emotion
  stream on the new setup.

### Replay API spike (22 Sep 2026) — `tools/replay/`

Riot's official Replay API renders a recorded game with fog of war off and the interface
reduced to the minimap, at an exact frame rate. `minimap_match.py` reads champion
positions off those frames (Data Dragon portrait templates, static-background exclusion,
one champion per icon). On a 30 s / 61-frame test at 3440×1440: 9 of 10 champions
placed correctly per frame, 44 game units per pixel. This replaces the planned
minimap-CV-on-screen-recording route and the dead `.rofl` parser. See `tools/replay/README.md`.

### Not changed, on purpose

- `auto_merge.py` / the server pipeline (separate repository): `AUTO_ALIGN_EMOTION` should
  become `False` now that the streams share a wall clock, and `eda` needs adding to
  `collect_files()`. `/data/gamestate/` needs creating in the SFTP container.
- `src/tobii_research.py` (a copy of the package's import shim), `enemy_detection.py`,
  `inference.py`, `merge_datasets.py`, `resize_video.py`, `nuanic_rings.py`, the
  notebooks: untouched, now behind optional dependency groups.
- Both `eye_tracking_script.py` and `Emotion_gaze_visualization.py` subscribe to the
  same Tobii. Untested whether that costs samples (`TESTING.md` T8).
