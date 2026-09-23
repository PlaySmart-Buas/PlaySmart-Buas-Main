"""Record League game state next to the other capture streams.

The League game process serves a small HTTPS API on ``https://127.0.0.1:2999``
for as long as a game is running. No API key, no rate limit, no network — it is
the local game talking to us. It answers in Practice Tool and customs as well as
matchmade games, which the Match-V5 web API does not.

What this buys the pipeline:

* **A clock mapping.** Every gamestate row carries ``unix_time_ms`` (the same
  base every other recorder stamps) *and* ``game_time_s`` (League's own clock).
  Sampled a few hundred times across a game, that pair lets any gaze sample,
  keypress or EDA reading be placed at "14:32 into the game" instead of
  "somewhere in a 74-minute recording".
* **Game boundaries.** The endpoint exists only during a game, so the
  orchestrator can bracket capture on it rather than on the operator noticing.
* **Labels for free.** Game mode, map and Riot ID come from the API instead of a
  free-text dropdown that produced nine spellings of two game names.

Note it does *not* give champion positions or the game id. The id is read from the
League client (``lcu.py``) into ``<sid>_meta.json`` so the post-game pipeline can find
the replay; positions come from rendering that replay (team repository,
``playsmart.replay``). Everything reconciles on game time.

Standalone (useful for testing without any lab hardware — open Practice Tool)::

    python src/liveclient_recorder.py --out data/gamestate --once

Imported by ``key_listener.py``::

    wait_for_game(...)  ->  record_game(sid, out_dir, stop_event)

The field names below follow Riot's documented payload but have not yet been
checked against a live game, so parsing is deliberately defensive: an unexpected
shape leaves a column blank rather than killing a capture session.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3

try:
    import session as playsmart_session
except ImportError:  # running from the repo root rather than src/
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import session as playsmart_session
import lcu

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://127.0.0.1:2999/liveclientdata"
POLL_HZ = 2.0

# The client serves a self-signed certificate. Riot publish riotgames.pem; set
# this to that path to validate properly instead of skipping verification.
VERIFY: "str | bool" = False

# How many consecutive failed polls mean the game is over rather than hitching.
MISS_LIMIT = 10

STATE_COLUMNS = [
    "session_id", "unix_time_ms", "game_time_s",
    "riot_id", "champion", "team", "position",
    "level", "current_gold", "kills", "deaths", "assists",
    "creep_score", "ward_score", "is_dead", "respawn_timer",
    "hp", "max_hp", "resource", "max_resource",
    "team_kills_order", "team_kills_chaos",
]

EVENT_COLUMNS = [
    "session_id", "unix_time_ms", "game_time_s",
    "event_id", "event_name",
    "killer", "victim", "assisters", "dragon_type", "stolen", "payload",
]


def now_ms() -> int:
    """Wall clock in milliseconds — the base every recorder shares."""
    return int(time.time() * 1000)


def poll(http: requests.Session) -> "dict | None":
    """One /allgamedata read. None means no game is running right now."""
    try:
        r = http.get(f"{BASE}/allgamedata", verify=VERIFY, timeout=2)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def dig(obj, *path, default=None):
    """Walk nested keys, returning default on any miss."""
    for key in path:
        try:
            obj = obj[key]
        except (KeyError, IndexError, TypeError):
            return default
    return obj if obj is not None else default


def active_name(data: dict) -> str:
    """Riot ID of the player at this machine.

    Older clients expose ``summonerName`` and newer ones ``riotId``; accept both
    so this keeps working across a patch boundary.
    """
    return (dig(data, "activePlayer", "riotId")
            or dig(data, "activePlayer", "summonerName") or "")


def find_active(data: dict) -> dict:
    """The active player's entry in allPlayers, matched on Riot ID."""
    name = active_name(data)
    for p in dig(data, "allPlayers", default=[]) or []:
        if (p.get("riotId") or p.get("summonerName")) == name:
            return p
    return {}


def state_row(sid: str, data: dict) -> dict:
    me = find_active(data)
    scores = me.get("scores", {}) or {}
    stats = dig(data, "activePlayer", "championStats", default={}) or {}

    kills = {"ORDER": 0, "CHAOS": 0}
    for p in dig(data, "allPlayers", default=[]) or []:
        team = p.get("team")
        if team in kills:
            kills[team] += int(dig(p, "scores", "kills", default=0) or 0)

    def num(v, ndigits=1):
        try:
            return round(float(v), ndigits)
        except (TypeError, ValueError):
            return ""

    return {
        "session_id": sid,
        "unix_time_ms": now_ms(),
        "game_time_s": num(dig(data, "gameData", "gameTime", default=0), 3),
        "riot_id": me.get("riotId") or me.get("summonerName") or active_name(data),
        "champion": me.get("championName", ""),
        "team": me.get("team", ""),
        "position": me.get("position", ""),
        "level": me.get("level", ""),
        "current_gold": num(dig(data, "activePlayer", "currentGold", default=0)),
        "kills": scores.get("kills", ""),
        "deaths": scores.get("deaths", ""),
        "assists": scores.get("assists", ""),
        "creep_score": scores.get("creepScore", ""),
        "ward_score": scores.get("wardScore", ""),
        "is_dead": me.get("isDead", ""),
        "respawn_timer": num(me.get("respawnTimer", 0)),
        "hp": num(stats.get("currentHealth", "")),
        "max_hp": num(stats.get("maxHealth", "")),
        "resource": num(stats.get("resourceValue", "")),
        "max_resource": num(stats.get("resourceMax", "")),
        "team_kills_order": kills["ORDER"],
        "team_kills_chaos": kills["CHAOS"],
    }


def event_rows(sid: str, data: dict, seen: "set") -> list:
    """New events since the last poll.

    The payload is cumulative — every poll returns the whole game's events — so
    dedupe on EventID. Timing comes from EventTime inside the payload, not from
    when we happened to poll, so a slow poll rate costs resolution on continuous
    state but not on event timing.

    **``game_time_s`` is the authoritative timestamp on this stream, not
    ``unix_time_ms``.** The wall clock here is stamped when the poller *saw* the
    event, which is up to one poll interval late — and much later than that for
    anything the cumulative payload backfills. A real capture recorded GameStart
    at ``game_time_s`` 0.022 with a wall clock 7.9 seconds after the fact,
    because recording began mid-game and the event arrived in the first payload.

    So when joining these events to gaze, input or audio, convert through the
    gamestate stream — which carries both clocks on every row — rather than using
    this column directly. Interpolate within a continuous stretch only; see
    ``check_session.py`` for why the mapping is piecewise and not a single slope.
    """
    rows = []
    for ev in dig(data, "events", "Events", default=[]) or []:
        eid = ev.get("EventID")
        if eid is None or eid in seen:
            continue
        seen.add(eid)
        rows.append({
            "session_id": sid,
            "unix_time_ms": now_ms(),
            "game_time_s": round(float(ev.get("EventTime", 0) or 0), 3),
            "event_id": eid,
            "event_name": ev.get("EventName", ""),
            "killer": ev.get("KillerName", ""),
            "victim": ev.get("VictimName", ""),
            "assisters": "|".join(ev.get("Assisters") or []),
            "dragon_type": ev.get("DragonType", ""),
            "stolen": ev.get("Stolen", ""),
            "payload": json.dumps(ev, separators=(",", ":")),
        })
    return rows


def meta_dict(sid: str, data: dict, started_ms: int) -> dict:
    me = find_active(data)
    return {
        "session_id": sid,
        "started_unix_ms": started_ms,
        "started_iso": datetime.fromtimestamp(started_ms / 1000, timezone.utc).isoformat(),
        "riot_id": me.get("riotId") or me.get("summonerName") or active_name(data),
        "champion": me.get("championName", ""),
        "team": me.get("team", ""),
        "position": me.get("position", ""),
        "game_mode": dig(data, "gameData", "gameMode", default=""),
        "map_name": dig(data, "gameData", "mapName", default=""),
        "map_number": dig(data, "gameData", "mapNumber", default=""),
        # Dragon soul terrain (Default / Infernal / Ocean / Mountain / Cloud /
        # Hextech / Chemtech). It changes the map's geometry, so gaze analysis
        # that cares about where things are on screen needs to know it.
        "map_terrain": dig(data, "gameData", "mapTerrain", default=""),
        # Bots in the lobby means this is not competitive gameplay. Recording it
        # per session is what lets a later filter separate real games from
        # practice — the distinction nobody could make in the old archive
        # without watching the footage.
        "has_bots": any(bool(p.get("isBot"))
                        for p in (dig(data, "allPlayers", default=[]) or [])),
        "roster": [
            {
                "riot_id": p.get("riotId") or p.get("summonerName"),
                "champion": p.get("championName"),
                "team": p.get("team"),
                "position": p.get("position"),
                "is_bot": bool(p.get("isBot", False)),
            }
            for p in (dig(data, "allPlayers", default=[]) or [])
        ],
    }


def add_identity(meta: dict, meta_file: Path) -> None:
    """Add the game id, platform, PUUID and client patch to the session's meta.

    These link a capture session to its replay (``<platform>-<game_id>.rofl``), its
    Match-V5 record, and the other players' sessions of the same game. Best-effort:
    on any failure the fields stay empty and ``identity_source`` says why; the
    post-game pipeline can still match the session to a replay by its roster.
    """
    try:
        ident = lcu.game_identity()
    except Exception as exc:  # never let this reach the recording thread
        ident = {"identity_source": f"error: {exc}"}
    meta.update(ident)
    try:
        meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[liveclient] could not add game id to meta: {exc}", flush=True)
        return
    if meta.get("game_id"):
        print(f"[liveclient] game {meta.get('platform_id') or '?'}_{meta['game_id']} "
              f"on client {meta.get('client_game_version') or '?'}", flush=True)
    else:
        print(f"[liveclient] no game id ({meta.get('identity_source')}); the replay "
              "will be matched by roster afterwards", flush=True)


def wait_for_game(abort: "threading.Event | None" = None,
                  poll_seconds: float = 2.0) -> "dict | None":
    """Block until a game is running. None if `abort` is set first."""
    http = requests.Session()
    while abort is None or not abort.is_set():
        data = poll(http)
        if data is not None:
            return data
        time.sleep(poll_seconds)
    return None


def record_game(sid: str, out_dir: Path, first: "dict | None" = None,
                stop: "threading.Event | None" = None,
                on_end: "threading.Event | None" = None) -> "dict | None":
    """Poll and write until the game ends or `stop` is set.

    Returns the meta dict so the caller can label the session without re-reading
    the file. `on_end` is set when the game itself finishes, which is what the
    orchestrator waits on.
    """
    http = requests.Session()
    data = first if first is not None else poll(http)
    if data is None:
        data = wait_for_game(abort=stop)
        if data is None:
            return None

    started = now_ms()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = meta_dict(sid, data, started)
    meta_file = out_dir / f"{sid}_meta.json"
    meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    # The game id comes from the League client, not this API; asking can take a few
    # seconds when the client is slow, so it runs beside the recording, never before it.
    threading.Thread(target=add_identity, args=(meta, meta_file), daemon=True).start()
    print(f"[liveclient] {meta['game_mode'] or 'game'} on {meta['map_name'] or '?'} "
          f"as {meta['champion'] or '?'} ({meta['riot_id'] or '?'})", flush=True)

    seen = set()
    period = 1.0 / POLL_HZ
    misses = 0

    state_file = out_dir / f"{sid}_gamestate.csv"
    event_file = out_dir / f"{sid}_events.csv"

    try:
        with state_file.open("w", newline="", encoding="utf-8") as sf, \
             event_file.open("w", newline="", encoding="utf-8") as ef:
            sw = csv.DictWriter(sf, fieldnames=STATE_COLUMNS)
            ew = csv.DictWriter(ef, fieldnames=EVENT_COLUMNS)
            sw.writeheader()
            ew.writeheader()

            while True:
                if stop is not None and stop.is_set():
                    print("[liveclient] stop requested", flush=True)
                    break

                tick = time.time()

                if data is not None:
                    misses = 0
                    sw.writerow(state_row(sid, data))
                    ended = False
                    for row in event_rows(sid, data, seen):
                        ew.writerow(row)
                        if row["event_name"] == "GameEnd":
                            print("[liveclient] GameEnd", flush=True)
                            ended = True
                    sf.flush()
                    ef.flush()
                    if ended:
                        break
                else:
                    # The endpoint drops before the post-game screen, and can
                    # hitch mid-game. Tolerate a few misses so one dropped
                    # request does not truncate a live recording.
                    misses += 1
                    if misses >= MISS_LIMIT:
                        print("[liveclient] client stopped responding — game over", flush=True)
                        break

                time.sleep(max(0.0, period - (time.time() - tick)))
                data = poll(http)
    finally:
        if on_end is not None:
            on_end.set()

    print(f"[liveclient] wrote {state_file.name} and {event_file.name}", flush=True)
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Record League game state from the Live Client Data API.")
    ap.add_argument("--out", default=None,
                    help="output directory (default: data/gamestate)")
    ap.add_argument("--once", action="store_true", help="record one game then exit")
    ap.add_argument("--verify", default=None,
                    help="path to riotgames.pem to validate the client certificate")
    args = ap.parse_args()

    global VERIFY
    if args.verify:
        VERIFY = args.verify

    out = Path(args.out) if args.out else playsmart_session.stream_dir("gamestate")

    while True:
        print("[liveclient] waiting for a game…", flush=True)
        data = wait_for_game()
        sid = playsmart_session.new_session_id()
        print(f"SESSION {sid}", flush=True)
        record_game(sid, out, first=data)
        if args.once:
            return
        time.sleep(5.0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[liveclient] stopped", flush=True)
