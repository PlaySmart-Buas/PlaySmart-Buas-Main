"""Label and upload one capture session.

Modified 2026-09 (iteration 5): rewritten - see below.

This used to be the weakest link in the pipeline. It asked the operator for a
player name and a game from a dropdown, then went looking for the newest
un-renamed file in each of data/gaze, data/input, data/emotion and data/eda and
assumed those belonged together. They frequently did not: if a recorder crashed,
the previous session's leftover file was the newest one and got adopted into
this session. The archive has a merged file whose gaze data is 2.6 hours from
the session named on it, and a machine holding eleven `merged_data_*.csv` with
identical checksums.

None of that guessing is needed any more. Every recorder now names its own file
`<session_id>_<stream>.csv` when it opens it, and `liveclient_recorder.py` writes
`<session_id>_meta.json` with the Riot ID, champion, game mode and map straight
from the game client. So this script just collects the files carrying this
session's id and uploads them.

The free-text game dropdown is gone with it — that field produced nine spellings
of two game names, including `valornat`, `valorant ` and `nogame`.
"""

import json
import os
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
sys.path.append(str(SRC.parent))

import session  # noqa: E402
from server.python_app.sftp_upload import upload_file_to_sftp  # noqa: E402

# Local stream -> remote SFTP directory.
#
# NOTE: /data/gamestate/ does not exist on the server yet. The sftp container's
# command in docker-compose.yml (PlaySmart-Server-Buas-Main) mkdir -p's each of
# these; gamestate needs adding there. Until it is, that one upload fails
# gracefully and the file stays local, which is fine.
UPLOAD_MAP = {
    "gaze": "/data/gaze/",
    "input": "/data/input/",
    "emotion": "/data/emotion/",
    "eda": "/data/eda/",
    "audio": "/data/audio/",
    "gamestate": "/data/gamestate/",
    "video": "/data/video/",
}

MAPPING_FILE = "data/json/ign_mapping.json"


# ------------------ participant pseudonymisation ------------------

def load_mapping(mapping_file=MAPPING_FILE):
    if os.path.exists(mapping_file):
        with open(mapping_file, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_mapping(mapping, mapping_file=MAPPING_FILE):
    os.makedirs(os.path.dirname(mapping_file), exist_ok=True)
    with open(mapping_file, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, indent=4)


def get_next_player_id(mapping):
    nums = [int(v[1:]) for v in mapping.values()
            if isinstance(v, str) and v.startswith("P") and v[1:].isdigit()]
    return f"P{max(nums, default=0) + 1:03d}"


def participant_for(riot_id):
    """Stable participant id for a Riot ID, minted on first sight.

    The mapping stays on this machine: it is the only way to honour a consent
    withdrawal later, so it must exist, and it must not travel with the data.
    """
    if not riot_id:
        return ""
    mapping = load_mapping()
    if riot_id not in mapping:
        mapping[riot_id] = get_next_player_id(mapping)
        save_mapping(mapping)
    return mapping[riot_id]


# ------------------ collecting this session's files ------------------

def read_meta(sid):
    path = session.stream_dir("gamestate") / f"{sid}_meta.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Could not read {path}: {exc}")
        return {}


def session_files(sid):
    """Every file this session's recorders produced, by stream."""
    found = {}
    for stream in UPLOAD_MAP:
        # video is included: obs_recorder.py files <sid>_video.<ext> and its
        # <sid>_video.json timing anchor under data/video before this runs.
        folder = session.data_dir() / stream
        if not folder.is_dir():
            continue
        matches = sorted(p for p in folder.iterdir()
                         if p.is_file() and p.name.startswith(sid))
        if matches:
            found[stream] = matches
    return found


def claim_video(sid, started_ms):
    """Move the OBS recording for this session into data/video.

    OBS names its own files by wall clock and knows nothing about session ids,
    so this is the one place a time-based match is still needed. Restricting it
    to files modified after the session began is what stops it from adopting an
    older recording — the failure mode that put a ten-minute rain video under a
    League session name in the old archive.
    """
    src_dir = Path(os.path.expanduser("~")) / "Videos"
    if not src_dir.is_dir():
        return None

    cutoff = started_ms / 1000.0 if started_ms else 0.0
    candidates = [p for p in src_dir.iterdir()
                  if p.suffix.lower() in (".mp4", ".mkv") and p.stat().st_mtime >= cutoff]
    if not candidates:
        print("No OBS recording found for this session.")
        return None

    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    dest = session.stream_dir("video") / f"{sid}_video{newest.suffix.lower()}"
    try:
        os.replace(newest, dest)
    except OSError as exc:
        print(f"Could not move {newest.name}: {exc}")
        return None
    print(f"Video: {newest.name} -> {dest.name}")
    return dest


# ------------------ main ------------------

def main():
    sid = session.session_id()
    meta = read_meta(sid)

    riot_id = meta.get("riot_id", "")
    participant = participant_for(riot_id)

    print(f"Session   : {sid}")
    print(f"Player    : {participant or '(unknown)'}"
          + (f"  [{riot_id}]" if riot_id else ""))
    print(f"Game      : {meta.get('game_mode') or 'unknown'} "
          f"on {meta.get('map_name') or 'unknown'} "
          f"as {meta.get('champion') or 'unknown'}")

    if meta:
        meta["participant_id"] = participant
        try:
            (session.stream_dir("gamestate") / f"{sid}_meta.json").write_text(
                json.dumps(meta, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"Could not update meta: {exc}")
    else:
        print("No gamestate meta for this session — the game client API was not "
              "reachable. Files will still upload, just without game labels.")

    # Give the recorders a moment to finish flushing to disk.
    time.sleep(2)

    files = session_files(sid)
    if "video" not in files:
        # obs_recorder did not run (OBS off, websocket disabled, library
        # missing): fall back to claiming a hand-started OBS recording by time.
        video = claim_video(sid, meta.get("started_unix_ms", 0))
        if video:
            files["video"] = [video]

    if not files:
        print("No files found for this session — nothing to upload.")
        return

    print("\nUploading:")
    for stream, paths in files.items():
        dest = UPLOAD_MAP.get(stream)
        if not dest:
            continue
        for path in paths:
            size = path.stat().st_size
            if size == 0:
                print(f"  !! {path.name} is empty — check the {stream} recorder")
            print(f"  {stream:<10} {path.name}  ({size/1e6:.2f} MB)")
            upload_file_to_sftp(str(path), dest)


if __name__ == "__main__":
    main()
