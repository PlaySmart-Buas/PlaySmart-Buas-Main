"""Read the League client's identity for the game being captured.

Added 2026-09 (iteration 5). Modified 2026-09-24: platform lookup falls back to the
chat identity and the client region.

The Live Client Data API (``liveclient_recorder.py``) says everything about the game
*in progress* except which game it is: there is no game id in its payload. Without
that id nothing links a capture session to its replay, its Match-V5 record, or to the
other four players' sessions of the same game. The League *client* (the lobby
process, "LCU") knows it, and keeps running while the game runs.

The LCU API is undocumented and unsupported by Riot, but tolerated and used by every
replay and overlay tool; it listens on a random port with a random password written
to a ``lockfile`` in the League install folder. Everything here is best-effort: a
missing client, a changed endpoint or a timeout returns an empty value and the
capture carries on. Only reads, never writes.

    python src/lcu.py            # print what the client reports right now
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ENV_LEAGUE_DIR = "PLAYSMART_LEAGUE_DIR"
DEFAULT_DIRS = [
    Path(r"C:\Riot Games\League of Legends"),
    Path(r"D:\Riot Games\League of Legends"),
    Path(r"C:\Program Files\Riot Games\League of Legends"),
]


def _lockfile_from_process() -> Path | None:
    """Find the lockfile next to a running LeagueClient.exe (non-default installs)."""
    try:
        import psutil
    except ImportError:
        return None
    for proc in psutil.process_iter(["name", "exe"]):
        try:
            if (proc.info["name"] or "").lower() in ("leagueclient.exe", "leagueclientux.exe"):
                exe = proc.info["exe"]
                if exe:
                    candidate = Path(exe).parent / "lockfile"
                    if candidate.is_file():
                        return candidate
        except Exception:  # noqa: BLE001 - access denied etc.; keep scanning
            continue
    return None


def lockfile() -> Path | None:
    """Path of the client's lockfile, or None when the client is not running."""
    dirs = [Path(os.environ[ENV_LEAGUE_DIR])] if os.environ.get(ENV_LEAGUE_DIR) else []
    for d in dirs + DEFAULT_DIRS:
        if (d / "lockfile").is_file():
            return d / "lockfile"
    return _lockfile_from_process()


class Client:
    """Minimal authenticated GET/POST against the local client API."""

    def __init__(self, port: int, password: str, timeout: float = 2.0):
        self.base = f"https://127.0.0.1:{port}"
        token = base64.b64encode(f"riot:{password}".encode()).decode()
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Basic {token}"
        self.http.verify = False
        self.timeout = timeout

    @classmethod
    def connect(cls, timeout: float = 2.0) -> Client | None:
        path = lockfile()
        if path is None:
            return None
        try:
            # name:pid:port:password:protocol
            _, _, port, password, _ = path.read_text(encoding="utf-8").strip().split(":")
            return cls(int(port), password, timeout)
        except (OSError, ValueError):
            return None

    def get(self, endpoint: str):
        """JSON body of a GET, or None on any failure."""
        try:
            r = self.http.get(self.base + endpoint, timeout=self.timeout)
            return r.json() if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            return None


REGION_TO_PLATFORM = {
    "EUW": "EUW1", "EUNE": "EUN1", "NA": "NA1", "KR": "KR", "JP": "JP1", "BR": "BR1",
    "LAN": "LA1", "LAS": "LA2", "OCE": "OC1", "TR": "TR1", "RU": "RU", "ME": "ME1",
    "SG": "SG2", "TW": "TW2", "VN": "VN2",
}


def platform_of(client: Client) -> str:
    """Platform of the logged-in account (``EUW1``), or "".

    Tried in order because Riot moves these between client versions: the login data
    packet (empty on the 24 Sep test, which left a game keyed ``UNKNOWN_<id>``), the
    chat identity, then the client's region mapped to its platform.
    """
    v = client.get("/lol-platform-config/v1/namespaces/LoginDataPacket/platformId")
    if isinstance(v, str) and v:
        return v.upper()
    v = (client.get("/lol-chat/v1/me") or {}).get("platformId")
    if isinstance(v, str) and v:
        return v.upper()
    region = (client.get("/riotclient/region-locale") or {}).get("region", "")
    return REGION_TO_PLATFORM.get(str(region).upper(), "")


def game_identity(client: Client | None = None) -> dict:
    """Everything the client knows that identifies the current game.

    Keys are always present; values are "" when unknown. ``game_id`` plus
    ``platform_id`` is the Riot match id (``EUW1_7990886946``) and the replay file
    name (``EUW1-7990886946.rofl``).
    """
    out = {"game_id": "", "platform_id": "", "queue_id": "", "puuid": "",
           "client_game_version": "", "identity_source": ""}
    client = client or Client.connect()
    if client is None:
        out["identity_source"] = "no League client found"
        return out

    session = client.get("/lol-gameflow/v1/session") or {}
    game = session.get("gameData") or {}
    if game.get("gameId"):
        out["game_id"] = str(game["gameId"])
    queue = game.get("queue") or {}
    if queue.get("id") is not None:
        out["queue_id"] = str(queue["id"])

    out["platform_id"] = platform_of(client)
    summoner = client.get("/lol-summoner/v1/current-summoner") or {}
    out["puuid"] = summoner.get("puuid", "") or ""
    version = client.get("/lol-patch/v1/game-version")
    if isinstance(version, str):
        out["client_game_version"] = version
    out["identity_source"] = "lcu" if out["game_id"] else "lcu (no game id in gameflow)"
    return out


if __name__ == "__main__":
    print(json.dumps(game_identity(), indent=2))
    sys.exit(0)
