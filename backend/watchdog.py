#!/usr/bin/env python3
"""
Muster watchdog.

Keeps the engine alive. If the server exits for any reason - crash, error,
someone killing the process - this restarts it within a few seconds. It also
starts Ollama if that is the configured chat backend and it is not running.

Registered as a Windows scheduled task by install_service.ps1, so it comes up
at logon and survives reboots.

To stop everything deliberately:  py backend/watchdog.py --stop
"""

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "backend" / "server.py"
LOG = ROOT / "data" / "watchdog.log"
STOP_FLAG = ROOT / "data" / ".stop"

CHECK_EVERY = 10        # seconds between health checks
GRACE = 6               # seconds to let a fresh start come up


def env() -> dict:
    e = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                e[k.strip()] = v.strip().strip('"').strip("'")
    return e


ENV = env()
PORT = int(ENV.get("API_PORT", 8770))
HEALTH = f"http://127.0.0.1:{PORT}/health"


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def healthy() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=4) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def ollama_up() -> bool:
    try:
        with urllib.request.urlopen(
                ENV.get("OLLAMA_HOST", "http://127.0.0.1:11434") + "/api/tags",
                timeout=4) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def spawn(args, name: str):
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    try:
        p = subprocess.Popen(
            args, cwd=str(ROOT), creationflags=flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL)
        log(f"started {name} (pid {p.pid})")
        return p
    except OSError as e:
        log(f"could not start {name}: {e}")
        return None


def stop_all() -> None:
    STOP_FLAG.write_text("stop", encoding="utf-8")
    log("stop flag set - watchdog will exit and leave the engine down")
    if os.name == "nt":
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-NetTCPConnection -LocalPort {PORT} -State Listen -EA SilentlyContinue |"
             " ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -EA SilentlyContinue }}"],
            capture_output=True)
    log("engine stopped")


def main() -> None:
    if "--stop" in sys.argv:
        stop_all()
        return

    STOP_FLAG.unlink(missing_ok=True)
    log(f"watchdog up - guarding {HEALTH}")

    fails = 0
    while True:
        if STOP_FLAG.exists():
            log("stop flag seen - exiting")
            return

        if ENV.get("CHAT_PROVIDER", "ollama").lower() == "ollama" and not ollama_up():
            spawn(["ollama", "serve"], "ollama")
            time.sleep(GRACE)

        if healthy():
            fails = 0
        else:
            fails += 1
            log(f"engine not responding ({fails}) - restarting")
            spawn([sys.executable, str(SERVER)], "muster engine")
            time.sleep(GRACE)
            if healthy():
                log("engine back up")
                fails = 0

        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("watchdog interrupted")
