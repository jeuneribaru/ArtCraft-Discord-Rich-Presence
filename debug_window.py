"""Diagnostic tool to inspect the active foreground window on Windows.

Prints every 2 seconds:
- Process name (.exe)
- Full executable path
- Exact foreground window title

Usage:
    python debug_window.py
"""

from __future__ import annotations

import sys
import time
from datetime import datetime

import psutil
import win32gui
import win32process

POLL_INTERVAL_SECONDS = 2.0


def get_active_window_details() -> dict[str, str]:
    """Return process name, executable path, PID, and title for the foreground window."""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return {
            "pid": "-",
            "process_name": "<none>",
            "exe_path": "<no foreground window>",
            "title": "",
        }

    title = win32gui.GetWindowText(hwnd) or ""
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    if pid <= 0:
        return {
            "pid": str(pid),
            "process_name": "<unknown>",
            "exe_path": "<invalid pid>",
            "title": title,
        }

    try:
        proc = psutil.Process(pid)
        proc_name = proc.name()
        try:
            exe_path = proc.exe()
        except (psutil.AccessDenied, psutil.ZombieProcess):
            exe_path = "<access denied>"
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as exc:
        proc_name = f"<error: {type(exc).__name__}>"
        exe_path = "<unavailable>"

    return {
        "pid": str(pid),
        "process_name": proc_name,
        "exe_path": exe_path,
        "title": title,
    }


def main() -> int:
    """Poll and display foreground window information every 2 seconds."""
    if sys.platform != "win32":
        print("Error: debug_window.py only supports Windows.", file=sys.stderr)
        return 1

    print("ArtCraft Window Inspector (Ctrl+C to quit)")
    print("Polling active foreground window every 2 seconds...\n")

    try:
        while True:
            now = datetime.now().strftime("%H:%M:%S")
            info = get_active_window_details()
            print(f"[{now}] PID: {info['pid']}")
            print(f"  Process : {info['process_name']}")
            print(f"  Path    : {info['exe_path']}")
            print(f"  Title   : {info['title']!r}")
            print("-" * 60)
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
