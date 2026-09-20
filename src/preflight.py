"""Pre-flight check for a capture PC: is everything the rig needs actually there?

Run it before arming a capture, every session, not only on setup day. It turns the
"did you plug in the tracker / is OBS open / is the password set" checklist into one
command with a pass/fail line per item, so a missing piece is found in ten seconds
instead of after a forty-minute game.

    poetry run python src/preflight.py            # everything
    poetry run python src/preflight.py --no-ble   # skip the 8 s ring scan
    poetry run python src/preflight.py --no-mic   # skip the 1 s microphone sample

Exit code 1 if anything the capture cannot run without is missing; 0 otherwise.
`warn` lines are things worth knowing that do not block a capture.

Added 2026-09 (iteration 5).
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import shutil
import socket
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent
REPO = SRC.parent
sys.path.insert(0, str(SRC))

import session  # noqa: E402  (loads .env)

OK, WARN, FAIL = " ok  ", "warn ", "FAIL "
_failures = 0


def report(flag: str, name: str, detail: str = "") -> None:
    global _failures
    if flag is FAIL:
        _failures += 1
    print(f"{flag} {name:<22} {detail}")


def skipped(label: str) -> bool:
    skip = {p.strip().lower() for p in os.environ.get("PLAYSMART_SKIP", "").split(",") if p.strip()}
    return label in skip


# ---------------------------------------------------------------- environment

def check_python() -> None:
    v = sys.version_info
    flag = OK if (v.major, v.minor) == (3, 10) else FAIL
    report(flag, "python", f"{platform.python_version()} at {sys.executable}"
           + ("" if flag is OK else "  (tobii-research needs 3.10: poetry env use 3.10)"))


def check_imports() -> None:
    needed = ["tobii_research", "pygame", "pynput", "keyboard", "sounddevice", "soundfile",
              "bleak", "requests", "obsws_python", "psutil", "pandas", "matplotlib",
              "paramiko", "screeninfo"]
    missing = []
    for name in needed:
        try:
            __import__(name)
        except Exception as exc:  # noqa: BLE001 - any import failure is the finding
            missing.append(f"{name} ({type(exc).__name__})")
    if missing:
        report(FAIL, "packages", "missing: " + ", ".join(missing) + "  -> poetry install")
    else:
        report(OK, "packages", f"{len(needed)} capture imports resolve")


def check_env_file() -> None:
    if session.ENV_FILE.is_file():
        report(OK, ".env", str(session.ENV_FILE))
    else:
        report(WARN, ".env", "not found - copy .env.example and fill in the passwords")
    if os.environ.get("PLAYSMART_OBS", "1") != "0" and not os.environ.get("PLAYSMART_OBS_PASSWORD"):
        report(WARN, "obs password", "PLAYSMART_OBS_PASSWORD empty - fine only if OBS auth is off")
    if not os.environ.get("PLAYSMART_SFTP_PASSWORD"):
        report(WARN, "sftp password", "PLAYSMART_SFTP_PASSWORD empty - files stay local, no upload")
    if os.environ.get("PLAYSMART_MOCK_GAZE") == "1":
        report(FAIL, "mock gaze", "PLAYSMART_MOCK_GAZE=1 is set - never record a real session with this")


def check_disk() -> None:
    root = session.data_dir().resolve()
    root.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(root).free / 1e9
    flag = OK if free_gb > 10 else (WARN if free_gb > 3 else FAIL)
    report(flag, "disk", f"{free_gb:.0f} GB free for {root}")


# ------------------------------------------------------------------ hardware

def check_display() -> None:
    try:
        import screeninfo
        monitors = screeninfo.get_monitors()
    except Exception as exc:  # noqa: BLE001
        report(WARN, "display", f"could not query monitors ({exc})")
        return
    if not monitors:
        report(FAIL, "display", "no monitor reported")
        return
    primary = monitors[0]
    detail = f"{primary.width}x{primary.height}" + (f", {len(monitors)} monitors" if len(monitors) > 1 else "")
    flag = OK if (primary.width, primary.height) == (1920, 1080) else WARN
    report(flag, "display", detail + ("" if flag is OK else "  (rig and OBS are set up for 1920x1080)"))
    if platform.system() == "Windows":
        try:
            import ctypes
            scale = ctypes.windll.shcore.GetScaleFactorForDevice(0)
            report(OK if scale == 100 else WARN, "scaling", f"{scale} %" + ("" if scale == 100 else "  (set 100 % or gaze and screen coordinates disagree)"))
        except Exception:  # noqa: BLE001
            pass


def check_tracker() -> None:
    if skipped("gaze"):
        report(WARN, "eye tracker", "skipped via PLAYSMART_SKIP")
        return
    try:
        import tobii_research as tr
        trackers = tr.find_all_eyetrackers()
    except Exception as exc:  # noqa: BLE001
        report(FAIL, "eye tracker", f"tobii_research failed ({exc})")
        return
    if not trackers:
        report(FAIL, "eye tracker", "none found - USB, Tobii service, or Eye Tracker Manager holding it")
        return
    t = trackers[0]
    try:
        hz = t.get_gaze_output_frequency()
    except Exception:  # noqa: BLE001
        hz = "?"
    report(OK, "eye tracker", f"{t.model} {t.serial_number} at {hz} Hz - calibrate the player in Eye Tracker Manager")


def check_openface() -> None:
    if os.environ.get("PLAYSMART_OPENFACE", "1") == "0" or skipped("emotion"):
        report(WARN, "openface", "disabled (PLAYSMART_OPENFACE=0 / PLAYSMART_SKIP) - no emotion stream")
        return
    exe = REPO / "OpenFace_2.2.0_win_x64" / "FeatureExtraction.exe"
    if not exe.is_file():
        report(FAIL, "openface", f"{exe.relative_to(REPO)} missing - setup.ps1 downloads it, or set PLAYSMART_OPENFACE=0")
        return
    models = exe.parent / "model" / "patch_experts"
    cen = list(models.glob("cen_patches_*.dat")) if models.is_dir() else []
    if len(cen) < 4:
        report(FAIL, "openface models", f"{len(cen)} cen_patches_*.dat in model\\patch_experts - run download_models.ps1 inside the OpenFace folder")
    else:
        report(OK, "openface", f"binary and {len(cen)} CEN patch-expert models present")
    try:
        import psutil
        stale = [p.pid for p in psutil.process_iter(["name"]) if "FeatureExtraction" in (p.info.get("name") or "")]
        if stale:
            report(WARN, "openface", f"leftover FeatureExtraction.exe running (pids {stale}) - key_listener kills it at start")
    except Exception:  # noqa: BLE001
        pass


def check_microphone(sample: bool) -> None:
    if skipped("audio"):
        report(WARN, "microphone", "skipped via PLAYSMART_SKIP")
        return
    try:
        import sounddevice as sd
        dev = sd.query_devices(kind="input")
    except Exception as exc:  # noqa: BLE001
        report(FAIL, "microphone", f"no default input device ({exc})")
        return
    name = dev.get("name", "?")
    if not sample:
        report(OK, "microphone", f"default input: {name}")
        return
    try:
        import numpy as np
        rec = sd.rec(int(1.0 * 44100), samplerate=44100, channels=1, dtype="float32")
        sd.wait()
        rms = float(np.sqrt(np.mean(rec ** 2)))
        flag = OK if rms > 1e-4 else WARN
        report(flag, "microphone", f"{name}, 1 s level {rms:.4f}" + ("" if flag is OK else "  (silent - muted, wrong device, or Windows privacy blocks desktop apps)"))
    except Exception as exc:  # noqa: BLE001
        report(WARN, "microphone", f"{name}; could not sample ({exc})")


def check_ring(scan: bool) -> None:
    if skipped("eda"):
        report(WARN, "eda ring", "skipped via PLAYSMART_SKIP=eda")
        return
    if not scan:
        report(WARN, "eda ring", "scan skipped (--no-ble)")
        return
    try:
        import asyncio

        from bleak import BleakScanner

        import nuanic_eda

        async def scan_once():
            found = await BleakScanner.discover(timeout=8.0, return_adv=True)
            hits = []
            for dev, adv in found.values():
                if nuanic_eda.NUANIC_SERVICE.lower() in [s.lower() for s in adv.service_uuids]:
                    hits.append(f"{dev.name} {dev.address}")
            return hits

        hits = asyncio.run(scan_once())
    except Exception as exc:  # noqa: BLE001
        report(WARN, "eda ring", f"BLE scan failed ({exc}) - Bluetooth off?")
        return
    if hits:
        report(OK, "eda ring", ", ".join(hits))
    else:
        report(WARN, "eda ring", "not advertising - charge it, unpair from phone/Windows, or set PLAYSMART_SKIP=eda")


# ------------------------------------------------------------------ software

def check_obs() -> None:
    if os.environ.get("PLAYSMART_OBS", "1") == "0":
        report(WARN, "obs", "disabled (PLAYSMART_OBS=0) - no video")
        return
    # The library logs a full traceback on a refused connection; the one-line
    # message below is the useful part.
    logging.getLogger("obsws_python").setLevel(logging.CRITICAL)
    try:
        import obsws_python as obs
        c = obs.ReqClient(host=os.environ.get("PLAYSMART_OBS_HOST", "127.0.0.1"),
                          port=int(os.environ.get("PLAYSMART_OBS_PORT", "4455")),
                          password=os.environ.get("PLAYSMART_OBS_PASSWORD", ""), timeout=3)
    except ImportError:
        report(FAIL, "obs", "obsws-python not installed - poetry install")
        return
    except Exception as exc:  # noqa: BLE001
        report(FAIL, "obs", f"cannot connect ({type(exc).__name__}: {exc}) - OBS open? WebSocket enabled? password?")
        return
    try:
        ver = c.get_version()
        ws = tuple(int(x) for x in ver.obs_web_socket_version.split(".")[:2])
        flag = OK if ws >= (5, 1) else WARN
        report(flag, "obs", f"OBS {ver.obs_version}, websocket {ver.obs_web_socket_version}"
               + ("" if flag is OK else "  (5.1+ needed for StopRecord to report the file path)"))
        if c.get_record_status().output_active:
            report(WARN, "obs recording", "already recording - obs_recorder will stop and restart it")
        vs = c.get_video_settings()
        res = f"{vs.base_width}x{vs.base_height} -> {vs.output_width}x{vs.output_height} @ {vs.fps_numerator / max(vs.fps_denominator, 1):.0f} fps"
        flag = OK if (vs.base_width, vs.base_height, vs.output_width, vs.output_height) == (1920, 1080, 1920, 1080) else WARN
        report(flag, "obs video", res + ("" if flag is OK else "  (set base and output to 1920x1080)"))
        try:
            scene = c.get_current_program_scene().current_program_scene_name
            items = c.get_scene_item_list(scene).scene_items
            kinds = [i.get("inputKind", "") for i in items]
            names = [i.get("sourceName", "") for i in items]
            if any(k == "game_capture" for k in kinds):
                report(OK, "obs scene", f"'{scene}': game capture present ({', '.join(names)})")
            elif any(k in ("window_capture", "monitor_capture") for k in kinds):
                report(WARN, "obs scene", f"'{scene}' uses window/display capture - use Game Capture of the League window (the overlay is a full-size window)")
            else:
                report(WARN, "obs scene", f"'{scene}' has no game capture source ({', '.join(names) or 'empty'})")
        except Exception:  # noqa: BLE001
            pass
        try:
            rec_dir = c.get_record_directory().record_directory
            report(OK, "obs recording dir", rec_dir)
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        report(WARN, "obs", f"connected but query failed ({exc})")


def check_league() -> None:
    try:
        import requests
        r = requests.get("https://127.0.0.1:2999/liveclientdata/gamestats", timeout=1.5, verify=False)
        mode = r.json().get("gameMode", "?")
        report(OK, "league", f"a game is running right now ({mode}) - the API answers")
    except Exception:  # noqa: BLE001
        report(OK, "league", "no game running (normal before F7); the API appears once a game loads")


def check_server() -> None:
    host = os.environ.get("PLAYSMART_SFTP_HOST", "10.4.28.2")
    port = int(os.environ.get("PLAYSMART_SFTP_PORT", "2422"))
    try:
        with socket.create_connection((host, port), timeout=3):
            report(OK, "sftp server", f"{host}:{port} reachable")
    except OSError as exc:
        report(WARN, "sftp server", f"{host}:{port} unreachable ({exc}) - BUas network / VPN? upload will fail, files stay local")


def main() -> None:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-ble", action="store_true", help="skip the Bluetooth scan for the EDA ring")
    ap.add_argument("--no-mic", action="store_true", help="skip the 1 s microphone level sample")
    args = ap.parse_args()

    print(f"PlaySmart pre-flight  {time.strftime('%Y-%m-%d %H:%M:%S')}  {platform.node()}  repo {REPO}\n")
    check_python()
    check_imports()
    check_env_file()
    check_disk()
    check_display()
    check_tracker()
    check_openface()
    check_microphone(sample=not args.no_mic)
    check_ring(scan=not args.no_ble)
    check_obs()
    check_league()
    check_server()
    print()
    if _failures:
        print(f"{_failures} blocking problem(s) above. Fix them before pressing F7.")
        raise SystemExit(1)
    print("Ready to capture. Calibrate the player, then run main.bat / key_listener.py and press F7.")


if __name__ == "__main__":
    main()
