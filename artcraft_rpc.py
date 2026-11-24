"""Core engine for ArtCraft Discord Rich Presence on Windows."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import signal
import sys
import threading
import time
import winreg
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Optional

import psutil
import pystray
import win32api
import win32event
import win32gui
import win32process
import winerror
from PIL import Image, ImageDraw, ImageFont
from pypresence import Presence

IS_FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else Path(__file__).resolve().parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))


def resolve_resource(filename: str) -> Path:
    """Resolve a data/config file, preferring external overrides next to the exe/script."""
    external = APP_DIR / filename
    if external.exists():
        return external
    return BUNDLE_DIR / filename


DEFAULT_CONFIG_PATH = resolve_resource("config.json")
DEFAULT_APPS_PATH = resolve_resource("apps.json")
DEFAULT_LOG_PATH = APP_DIR / "artcraft_rpc.log"
PLACEHOLDER_CLIENT_ID = "YOUR_DISCORD_CLIENT_ID_HERE"
STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_REG_NAME = "ArtCraftRPC"
SINGLE_INSTANCE_MUTEX = "ArtCraftDiscordRichPresenceMutex"

LOGGER = logging.getLogger("artcraft_rpc")


@dataclass(frozen=True)
class RuntimeConfig:
    """Global configuration loaded from config.json."""

    discord_client_id: str
    small_image: str = "artcraft_logo"
    small_text: str = "ArtCraft Suite"
    refresh_interval: float = 3.0
    privacy_mode: bool = False
    headless: bool = False
    icon_path: str = "icon.ico"


@dataclass(frozen=True)
class AppConfig:
    """Per-application configuration loaded from apps.json."""

    process_name: str
    app_name: str
    large_image: str
    large_text: str
    action_verb: str = "Editing"
    idle_state: str = "Idling"
    title_separator: str = " - "
    title_regex: Optional[re.Pattern[str]] = None
    idle_titles: frozenset[str] = field(default_factory=frozenset)
    open_file_extensions: tuple[str, ...] = ()
    open_file_mode: str = "filename"


@dataclass(frozen=True)
class WindowInfo:
    """Snapshot of a Windows window and its owning process."""

    hwnd: int
    pid: int
    process_name: str
    title: str


def setup_logging(debug: bool = False) -> None:
    """Configure rotating file logging and optional console logging."""
    LOGGER.setLevel(logging.DEBUG if debug else logging.INFO)
    LOGGER.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        file_handler = RotatingFileHandler(
            DEFAULT_LOG_PATH,
            maxBytes=256 * 1024,
            backupCount=1,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG if debug else logging.INFO)
        file_handler.setFormatter(formatter)
        LOGGER.addHandler(file_handler)
    except OSError:
        # Ignore file logging failures if directory is read-only.
        pass

    stream = sys.stderr or sys.stdout
    if stream is not None and (debug or not getattr(sys, "frozen", False)):
        console_handler = logging.StreamHandler(stream)
        console_handler.setLevel(logging.DEBUG if debug else logging.INFO)
        console_handler.setFormatter(formatter)
        LOGGER.addHandler(console_handler)


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> RuntimeConfig:
    """Load global configuration from JSON file."""
    raw: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, dict):
                raw = data
    else:
        LOGGER.warning("Config file not found at %s; using defaults.", config_path)

    client_id = str(raw.get("DISCORD_CLIENT_ID") or "").strip()
    refresh_raw = raw.get("refresh_interval", 3)
    try:
        refresh_interval = max(1.0, float(refresh_raw))
    except (TypeError, ValueError):
        refresh_interval = 3.0

    return RuntimeConfig(
        discord_client_id=client_id,
        small_image=str(raw.get("small_image", "artcraft_logo")).strip(),
        small_text=str(raw.get("small_text", "ArtCraft Suite")).strip(),
        refresh_interval=refresh_interval,
        privacy_mode=bool(raw.get("privacy_mode", False)),
        headless=bool(raw.get("headless", False)),
        icon_path=str(raw.get("icon_path", "icon.ico")).strip(),
    )


def load_apps(apps_path: Path = DEFAULT_APPS_PATH) -> dict[str, AppConfig]:
    """Load supported applications from apps.json, keyed by lowercase process name."""
    if not apps_path.exists():
        LOGGER.error("Apps configuration file not found at %s.", apps_path)
        return {}

    with apps_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    entries = raw.get("apps", []) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        LOGGER.error("Invalid apps.json format: expected a list of app entries.")
        return {}

    apps: dict[str, AppConfig] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        process_name = str(entry.get("process_name", "")).strip().lower()
        app_name = str(entry.get("app_name", "")).strip()
        if not process_name or not app_name:
            continue

        compiled_regex: Optional[re.Pattern[str]] = None
        regex_str = str(entry.get("title_regex") or "").strip()
        if regex_str:
            try:
                compiled_regex = re.compile(regex_str, re.IGNORECASE)
            except re.error as exc:
                LOGGER.error("Invalid title_regex for %s: %s", app_name, exc)

        idle_titles_raw = entry.get("idle_titles") or []
        idle_titles = frozenset(
            str(item).strip().lower()
            for item in idle_titles_raw
            if str(item).strip()
        )

        ext_raw = entry.get("open_file_extensions") or []
        open_file_extensions = tuple(
            (ext if ext.startswith(".") else f".{ext}")
            for ext in (str(item).strip().lower() for item in ext_raw)
            if ext
        )

        open_file_mode = str(entry.get("open_file_mode", "filename")).strip().lower()
        if open_file_mode not in ("filename", "parent_dir"):
            open_file_mode = "filename"

        apps[process_name] = AppConfig(
            process_name=process_name,
            app_name=app_name,
            large_image=str(entry.get("large_image", "")).strip(),
            large_text=str(entry.get("large_text", app_name)).strip(),
            action_verb=str(entry.get("action_verb", "Editing")).strip() or "Editing",
            idle_state=str(entry.get("idle_state", "Idling")).strip() or "Idling",
            title_separator=str(entry.get("title_separator", " - ")),
            title_regex=compiled_regex,
            idle_titles=idle_titles,
            open_file_extensions=open_file_extensions,
            open_file_mode=open_file_mode,
        )

    return apps


def _clean_extracted_filename(raw_name: str, app: AppConfig) -> Optional[str]:
    """Normalize extracted title segment and filter out idle placeholders."""
    name = raw_name.strip()
    if name.startswith("*"):
        name = name[1:].strip()
    if not name:
        return None
    lowered = name.lower()
    if lowered == app.app_name.lower() or lowered in app.idle_titles:
        return None
    return name


def extract_filename(app: AppConfig, window_title: str) -> Optional[str]:
    """Extract the active document/file name from an application's window title."""
    title = (window_title or "").strip()
    if not title:
        return None

    lowered = title.lower()
    if lowered == app.app_name.lower() or lowered in app.idle_titles:
        return None

    if app.title_regex is not None:
        match = app.title_regex.search(title)
        if match:
            if "filename" in app.title_regex.groupindex:
                candidate = match.group("filename")
            elif app.title_regex.groups >= 1:
                candidate = match.group(1)
            else:
                candidate = match.group(0)
            cleaned = _clean_extracted_filename(candidate, app)
            if cleaned is not None:
                return cleaned
            return None

    sep = app.title_separator
    if sep and sep in title:
        parts = [part.strip() for part in title.split(sep)]
        if len(parts) >= 2:
            app_lower = app.app_name.lower()
            if parts[-1].lower() == app_lower or app_lower in parts[-1].lower():
                candidate = sep.join(parts[:-1])
                return _clean_extracted_filename(candidate, app)
            if parts[0].lower() == app_lower or app_lower in parts[0].lower():
                candidate = sep.join(parts[1:])
                return _clean_extracted_filename(candidate, app)

            non_app_parts = [
                p for p in parts
                if p and p.lower() != app_lower and p.lower() not in app.idle_titles
            ]
            if non_app_parts:
                return _clean_extracted_filename(non_app_parts[0], app)

    return None


def _clamp_discord_text(text: str, max_len: int = 128) -> str:
    """Enforce Discord Rich Presence string length bounds (2..128 chars)."""
    cleaned = text.strip()
    if len(cleaned) == 1:
        return f"{cleaned} "
    if len(cleaned) > max_len:
        return f"{cleaned[: max_len - 3]}..."
    return cleaned


def _is_ignored_open_file(path_str: str, exe_dir_lower: str) -> bool:
    """Return True if an open file belongs to app install, Windows, or cache dirs."""
    lowered = path_str.lower()
    if exe_dir_lower and lowered.startswith(exe_dir_lower):
        return True
    ignored_markers = (
        "\\windows\\",
        "\\appdata\\local\\temp\\",
        "\\appdata\\roaming\\artcraft\\",
        "\\appdata\\local\\artcraft\\",
    )
    return any(marker in lowered for marker in ignored_markers)


GENERIC_PARENT_DIRS = frozenset({
    "videos",
    "vidéos",
    "desktop",
    "bureau",
    "downloads",
    "téléchargements",
    "documents",
    "music",
    "musique",
    "pictures",
    "images",
})


def _format_open_file_label(file_path: Path, mode: str) -> str:
    """Format an open file path as filename or meaningful parent directory name."""
    if mode == "parent_dir":
        parent_name = file_path.parent.name.strip()
        if parent_name and parent_name.lower() not in GENERIC_PARENT_DIRS:
            return parent_name
    return file_path.name


def find_open_filename_for_app(
    app: AppConfig,
    preferred_pid: Optional[int] = None,
) -> Optional[str]:
    """Fallback: inspect open file handles for apps that omit filenames from window titles."""
    if not app.open_file_extensions:
        return None

    ext_priority = {ext: idx for idx, ext in enumerate(app.open_file_extensions)}
    pids: list[int] = []
    seen_pids: set[int] = set()

    if preferred_pid:
        pids.append(preferred_pid)
        seen_pids.add(preferred_pid)

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name == app.process_name:
                pid = int(proc.info["pid"])
                if pid not in seen_pids:
                    pids.append(pid)
                    seen_pids.add(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    best_rank: Optional[int] = None
    best_path: Optional[Path] = None

    for pid in pids:
        try:
            proc = psutil.Process(pid)
            procs_to_check = [proc]
            try:
                procs_to_check.extend(proc.children(recursive=True))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

            for target_proc in procs_to_check:
                try:
                    try:
                        exe_dir_lower = str(Path(target_proc.exe()).resolve().parent).lower() + "\\"
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                        exe_dir_lower = ""

                    for open_file in target_proc.open_files():
                        file_path = open_file.path
                        if _is_ignored_open_file(file_path, exe_dir_lower):
                            continue
                        candidate = Path(file_path)
                        suffix = candidate.suffix.lower()
                        rank = ext_priority.get(suffix)
                        if rank is not None:
                            LOGGER.debug("Open file candidate for %s: %s", app.app_name, file_path)
                            if best_rank is None or rank < best_rank:
                                best_rank = rank
                                best_path = candidate
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    if best_path is None:
        return None
    return _format_open_file_label(best_path, app.open_file_mode)


def build_presence_payload(
    app: AppConfig,
    window_title: str,
    session_start: int,
    config: RuntimeConfig,
    privacy_mode: bool,
    fallback_filename: Optional[str] = None,
) -> dict[str, Any]:
    """Build the pypresence update dictionary for an active ArtCraft app."""
    if privacy_mode:
        state = "Working on a file"
    else:
        filename = extract_filename(app, window_title) or fallback_filename
        if filename:
            state = _clamp_discord_text(f"{app.action_verb} {filename}")
        else:
            state = _clamp_discord_text(app.idle_state)

    payload: dict[str, Any] = {
        "details": _clamp_discord_text(app.app_name),
        "state": state,
        "start": session_start,
    }

    if app.large_image:
        payload["large_image"] = app.large_image
        if app.large_text:
            payload["large_text"] = _clamp_discord_text(app.large_text)

    if config.small_image:
        payload["small_image"] = config.small_image
        if config.small_text:
            payload["small_text"] = _clamp_discord_text(config.small_text)

    return payload


def get_foreground_window() -> Optional[WindowInfo]:
    """Return WindowInfo for the current foreground window on Windows."""
    try:
        hwnd = win32gui.GetForegroundWindow()
    except Exception:
        return None

    if not hwnd:
        return None

    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid <= 0:
            return None
        proc = psutil.Process(pid)
        proc_name = proc.name().lower()
        title = win32gui.GetWindowText(hwnd) or ""
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, Exception):
        return None

    return WindowInfo(hwnd=hwnd, pid=pid, process_name=proc_name, title=title)


def get_window_title_if_valid(hwnd: Optional[int]) -> Optional[str]:
    """Read a window's title if its HWND is still valid."""
    if not hwnd:
        return None
    try:
        if win32gui.IsWindow(hwnd):
            return win32gui.GetWindowText(hwnd) or ""
    except Exception:
        return None
    return None


def find_pid_for_process(process_name: str, preferred_pid: Optional[int] = None) -> Optional[int]:
    """Return an active PID for process_name, preferring preferred_pid if still alive."""
    target = process_name.lower()
    if preferred_pid:
        try:
            proc = psutil.Process(preferred_pid)
            if (
                proc.is_running()
                and proc.status() != psutil.STATUS_ZOMBIE
                and proc.name().lower() == target
            ):
                return preferred_pid
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name == target:
                return int(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return None


def find_first_window_for_pid(pid: int) -> tuple[Optional[int], str]:
    """Find a visible top-level window and title owned by pid."""
    result: list[tuple[int, str]] = []

    def _enum_cb(hwnd: int, _lparam: Any) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _, win_pid = win32process.GetWindowThreadProcessId(hwnd)
            if win_pid == pid:
                title = win32gui.GetWindowText(hwnd) or ""
                if title:
                    result.append((hwnd, title))
                    return False
                if not result:
                    result.append((hwnd, ""))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_enum_cb, None)
    except Exception:
        pass

    if result:
        return result[0]
    return None, ""


def find_any_running_app(
    apps_by_process: dict[str, AppConfig],
) -> Optional[tuple[AppConfig, int, Optional[int], str]]:
    """Find any supported ArtCraft app currently running in the background."""
    if not apps_by_process:
        return None

    fallback_match: Optional[tuple[AppConfig, int, Optional[int], str]] = None
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            app = apps_by_process.get(name)
            if app is not None:
                pid = int(proc.info["pid"])
                hwnd, title = find_first_window_for_pid(pid)
                if title:
                    return app, pid, hwnd, title
                if fallback_match is None:
                    fallback_match = (app, pid, hwnd, title)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return fallback_match


class DiscordRPCClient:
    """Resilient wrapper around pypresence.Presence with auto-reconnect."""

    def __init__(self, client_id: str, reconnect_delay: float = 5.0) -> None:
        self.client_id = client_id
        self.reconnect_delay = reconnect_delay
        self._rpc: Optional[Presence] = None
        self._connected = False
        self._last_attempt = 0.0
        self._last_payload: Optional[dict[str, Any]] = None

    @property
    def is_configured(self) -> bool:
        """Return True if a non-placeholder numeric Discord Client ID is set."""
        return bool(
            self.client_id
            and self.client_id != PLACEHOLDER_CLIENT_ID
            and self.client_id.isdigit()
        )

    @staticmethod
    def _ensure_event_loop() -> None:
        """Ensure the current thread has an asyncio event loop for pypresence."""
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

    def ensure_connected(self) -> bool:
        """Connect to Discord IPC if not already connected, respecting backoff."""
        if not self.is_configured:
            return False
        if self._connected and self._rpc is not None:
            return True

        now = time.monotonic()
        if now - self._last_attempt < self.reconnect_delay:
            return False

        self._last_attempt = now
        self._ensure_event_loop()

        try:
            rpc = Presence(self.client_id)
            rpc.connect()
            self._rpc = rpc
            self._connected = True
            self._last_payload = None
            LOGGER.info("Connected to Discord Rich Presence.")
            return True
        except Exception as exc:
            LOGGER.debug("Discord connection unavailable: %s", exc)
            self._rpc = None
            self._connected = False
            return False

    def update(self, payload: dict[str, Any]) -> bool:
        """Send presence update if payload changed or after a reconnect."""
        if not self.ensure_connected():
            return False

        if payload == self._last_payload:
            return True

        assert self._rpc is not None
        try:
            self._rpc.update(**payload)
            self._last_payload = dict(payload)
            LOGGER.info("Updated Discord RPC: %s - %s", payload.get("details"), payload.get("state"))
            LOGGER.debug("Updated Discord RPC payload: %s", payload)
            return True
        except Exception as exc:
            LOGGER.warning("Discord RPC update failed (%s); will reconnect.", exc)
            self.disconnect()
            return False

    def clear(self) -> None:
        """Clear active Discord Rich Presence without crashing if disconnected."""
        if not self._connected or self._rpc is None:
            self._last_payload = None
            return
        if self._last_payload is None:
            return

        try:
            self._rpc.clear()
            LOGGER.debug("Cleared Discord RPC.")
        except Exception as exc:
            LOGGER.debug("Failed to clear Discord RPC cleanly: %s", exc)
            self.disconnect()
        finally:
            self._last_payload = None

    def disconnect(self) -> None:
        """Close the Discord IPC socket cleanly."""
        if self._rpc is not None:
            try:
                self._rpc.close()
            except Exception:
                pass
        self._rpc = None
        self._connected = False
        self._last_payload = None


class ArtCraftRPCService:
    """Coordinates foreground/background app detection and Discord RPC updates."""

    def __init__(self, config: RuntimeConfig, apps: dict[str, AppConfig]) -> None:
        self.config = config
        self.apps = apps
        self._lock = threading.Lock()
        self._paused = False
        self._privacy_mode = config.privacy_mode
        self._status_text = "Idle"
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._status_callback: Optional[Callable[[], None]] = None

        self._rpc = DiscordRPCClient(config.discord_client_id)
        self._current_app: Optional[AppConfig] = None
        self._current_pid: Optional[int] = None
        self._current_hwnd: Optional[int] = None
        self._last_title: str = ""
        self._session_start: Optional[int] = None
        self._apps_mtime: Optional[float] = self._get_apps_mtime()

    @staticmethod
    def _get_apps_mtime() -> Optional[float]:
        try:
            return resolve_resource("apps.json").stat().st_mtime
        except OSError:
            return None

    def _maybe_reload_apps(self) -> None:
        """Reload apps.json automatically when the file changes on disk."""
        mtime = self._get_apps_mtime()
        if mtime is None or mtime == self._apps_mtime:
            return
        try:
            new_apps = load_apps(resolve_resource("apps.json"))
        except Exception as exc:
            LOGGER.warning("Failed to reload apps.json (%s); keeping previous config.", exc)
            self._apps_mtime = mtime
            return

        self._apps_mtime = mtime
        self.apps = new_apps
        if self._current_app is not None:
            self._current_app = new_apps.get(self._current_app.process_name)
        LOGGER.info("Reloaded apps.json (%d apps).", len(new_apps))

    def set_status_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """Register callback invoked whenever tray-visible status changes."""
        self._status_callback = callback

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def privacy_mode(self) -> bool:
        with self._lock:
            return self._privacy_mode

    @property
    def status_text(self) -> str:
        with self._lock:
            return self._status_text

    def toggle_paused(self) -> bool:
        with self._lock:
            self._paused = not self._paused
            new_state = self._paused
        self._wake_event.set()
        return new_state

    def toggle_privacy_mode(self) -> bool:
        with self._lock:
            self._privacy_mode = not self._privacy_mode
            new_state = self._privacy_mode
        self._wake_event.set()
        return new_state

    def stop(self) -> None:
        """Signal the service loop to stop and wake immediately."""
        self._stop_event.set()
        self._wake_event.set()

    def _set_status(self, text: str) -> None:
        changed = False
        with self._lock:
            if self._status_text != text:
                self._status_text = text
                changed = True
        if changed and self._status_callback is not None:
            try:
                self._status_callback()
            except Exception:
                pass

    def _reset_session(self) -> None:
        self._current_app = None
        self._current_pid = None
        self._current_hwnd = None
        self._last_title = ""
        self._session_start = None

    def _tick(self) -> None:
        self._maybe_reload_apps()

        if self.paused:
            self._reset_session()
            self._rpc.clear()
            self._set_status("Paused")
            return

        if not self._rpc.is_configured:
            self._set_status("Config: Set Client ID")
            return

        fg = get_foreground_window()
        fg_app = self.apps.get(fg.process_name) if fg is not None else None

        if fg is not None and fg_app is not None:
            # Foreground window is a supported ArtCraft app.
            if self._current_app is None or fg_app.process_name != self._current_app.process_name:
                self._current_app = fg_app
                self._session_start = int(time.time())
                LOGGER.info("Detected active ArtCraft app: %s", fg_app.app_name)
            self._current_pid = fg.pid
            self._current_hwnd = fg.hwnd
            self._last_title = fg.title
        elif self._current_app is not None:
            # Anti-flicker: keep active app while it still runs in background.
            alive_pid = find_pid_for_process(
                self._current_app.process_name,
                preferred_pid=self._current_pid,
            )
            if alive_pid is not None:
                self._current_pid = alive_pid
                bg_title = get_window_title_if_valid(self._current_hwnd)
                if bg_title is not None:
                    self._last_title = bg_title
            else:
                # Active app closed; switch to another running ArtCraft app or clear.
                fallback = find_any_running_app(self.apps)
                if fallback is not None:
                    fb_app, fb_pid, fb_hwnd, fb_title = fallback
                    self._current_app = fb_app
                    self._current_pid = fb_pid
                    self._current_hwnd = fb_hwnd
                    self._last_title = fb_title
                    self._session_start = int(time.time())
                    LOGGER.info("Switched to background ArtCraft app: %s", fb_app.app_name)
                else:
                    LOGGER.info("All ArtCraft apps closed; clearing presence.")
                    self._reset_session()
                    self._rpc.clear()
                    self._set_status("Idle")
                    return
        else:
            # No current session; check if an ArtCraft app is already open in background.
            bg_app = find_any_running_app(self.apps)
            if bg_app is None:
                self._rpc.clear()
                self._set_status("Idle")
                return
            app, pid, hwnd, title = bg_app
            self._current_app = app
            self._current_pid = pid
            self._current_hwnd = hwnd
            self._last_title = title
            self._session_start = int(time.time())
            LOGGER.info("Detected running ArtCraft app: %s", app.app_name)

        assert self._current_app is not None
        assert self._session_start is not None

        fallback_filename: Optional[str] = None
        if (
            not self.privacy_mode
            and self._current_app.open_file_extensions
            and extract_filename(self._current_app, self._last_title) is None
        ):
            fallback_filename = find_open_filename_for_app(
                self._current_app,
                preferred_pid=self._current_pid,
            )

        payload = build_presence_payload(
            app=self._current_app,
            window_title=self._last_title,
            session_start=self._session_start,
            config=self.config,
            privacy_mode=self.privacy_mode,
            fallback_filename=fallback_filename,
        )
        connected = self._rpc.update(payload)
        if connected:
            self._set_status(f"Active: {self._current_app.app_name}")
        else:
            self._set_status(f"Waiting for Discord ({self._current_app.app_name})")

    def run_forever(self) -> None:
        """Main polling loop until stop() is called."""
        if not self._rpc.is_configured:
            LOGGER.warning(
                "DISCORD_CLIENT_ID is not configured in config.json. "
                "Update it with your Discord Application ID."
            )

        try:
            while not self._stop_event.is_set():
                try:
                    self._tick()
                except Exception as exc:
                    LOGGER.exception("Unexpected error in RPC loop: %s", exc)

                self._wake_event.wait(timeout=self.config.refresh_interval)
                self._wake_event.clear()
        finally:
            self._rpc.clear()
            self._rpc.disconnect()


def create_default_tray_icon() -> Image.Image:
    """Generate a clean 64x64 ArtCraft icon with Pillow when no .ico exists."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded indigo badge
    try:
        draw.rounded_rectangle(
            [(2, 2), (size - 3, size - 3)],
            radius=14,
            fill=(88, 101, 242, 255),
            outline=(255, 255, 255, 220),
            width=2,
        )
    except AttributeError:
        draw.rectangle([(2, 2), (size - 3, size - 3)], fill=(88, 101, 242, 255))

    # Stylized "AC" monogram centered
    font = ImageFont.load_default()
    text = "AC"
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (size - tw) / 2 - bbox[0]
        ty = (size - th) / 2 - bbox[1]
    except AttributeError:
        tx, ty = 22, 24

    # Draw subtle craft diamond behind text
    draw.polygon(
        [(32, 10), (52, 32), (32, 54), (12, 32)],
        outline=(255, 255, 255, 90),
    )
    draw.text((tx, ty), text, fill=(255, 255, 255, 255), font=font)
    return img


def load_tray_icon(icon_path_str: str) -> Image.Image:
    """Load icon file from disk if available, otherwise generate default icon."""
    if icon_path_str:
        candidate = Path(icon_path_str)
        if not candidate.is_absolute():
            candidate = resolve_resource(icon_path_str)
        if candidate.is_file():
            try:
                return Image.open(candidate)
            except Exception as exc:
                LOGGER.warning("Could not open icon %s (%s); using default.", candidate, exc)
    return create_default_tray_icon()


def get_startup_command() -> str:
    """Build the Windows Run command for either the frozen exe or main.pyw."""
    exe = Path(sys.executable).resolve()
    if IS_FROZEN:
        return f'"{exe}"'
    pythonw = exe.with_name("pythonw.exe")
    launcher = pythonw if pythonw.is_file() else exe
    script = APP_DIR / "main.pyw"
    if not script.is_file():
        script = APP_DIR / "main.py"
    return f'"{launcher}" "{script}"'


def is_startup_enabled() -> bool:
    """Return True if ArtCraftRPC is registered to launch at Windows startup."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, STARTUP_REG_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_startup_enabled(enabled: bool) -> bool:
    """Enable or disable automatic launch at Windows user logon."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            STARTUP_REG_PATH,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(key, STARTUP_REG_NAME, 0, winreg.REG_SZ, get_startup_command())
            else:
                try:
                    winreg.DeleteValue(key, STARTUP_REG_NAME)
                except FileNotFoundError:
                    pass
        LOGGER.info("Windows startup %s.", "enabled" if enabled else "disabled")
        return True
    except OSError as exc:
        LOGGER.warning("Failed to update Windows startup setting: %s", exc)
        return False


def acquire_single_instance_mutex() -> Optional[Any]:
    """Acquire a named Windows mutex so only one instance runs at a time."""
    try:
        handle = win32event.CreateMutex(None, False, SINGLE_INSTANCE_MUTEX)
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            win32api.CloseHandle(handle)
            return None
        return handle
    except Exception as exc:
        LOGGER.debug("Mutex creation skipped: %s", exc)
        return object()


def run_with_tray(service: ArtCraftRPCService, config: RuntimeConfig) -> None:
    """Run the RPC service in a background thread with a pystray system tray icon."""
    image = load_tray_icon(config.icon_path)

    def _on_toggle_pause(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        service.toggle_paused()
        icon.update_menu()

    def _on_toggle_privacy(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        service.toggle_privacy_mode()
        icon.update_menu()

    def _on_toggle_startup(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        set_startup_enabled(not is_startup_enabled())
        icon.update_menu()

    def _on_quit(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        service.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(lambda _item: service.status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda _item: "Resume" if service.paused else "Pause",
            _on_toggle_pause,
        ),
        pystray.MenuItem(
            "Privacy Mode",
            _on_toggle_privacy,
            checked=lambda _item: service.privacy_mode,
        ),
        pystray.MenuItem(
            "Run at Startup",
            _on_toggle_startup,
            checked=lambda _item: is_startup_enabled(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _on_quit),
    )

    tray_icon = pystray.Icon("artcraft_rpc", image, "ArtCraft Rich Presence", menu)
    service.set_status_callback(tray_icon.update_menu)

    worker = threading.Thread(
        target=service.run_forever,
        name="ArtCraftRPCWorker",
        daemon=True,
    )

    def _on_setup(icon: pystray.Icon) -> None:
        icon.visible = True
        worker.start()

    try:
        tray_icon.run(setup=_on_setup)
    finally:
        service.stop()
        if worker.is_alive():
            worker.join(timeout=3.0)


def run_headless(service: ArtCraftRPCService) -> None:
    """Run the RPC service without any system tray icon."""
    def _handle_signal(_signum: int, _frame: Any) -> None:
        service.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):
            pass

    try:
        service.run_forever()
    except KeyboardInterrupt:
        service.stop()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI options safely even when running under pythonw.exe."""
    parser = argparse.ArgumentParser(
        description="ArtCraft Discord Rich Presence utility",
        add_help=sys.stderr is not None,
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run completely hidden without a system tray icon.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    return parser.parse_args(argv)


def run_cli(argv: Optional[list[str]] = None) -> int:
    """Application entry point shared by main.py and main.pyw."""
    if sys.platform != "win32":
        if sys.stderr is not None:
            print("Error: ArtCraft Discord Rich Presence only supports Windows.", file=sys.stderr)
        return 1

    args = parse_args(argv)
    setup_logging(debug=args.debug)

    mutex = acquire_single_instance_mutex()
    if mutex is None:
        LOGGER.info("ArtCraft RPC is already running; exiting duplicate instance.")
        return 0

    config = load_config(resolve_resource("config.json"))
    apps = load_apps(resolve_resource("apps.json"))
    if not apps:
        LOGGER.warning("No applications loaded from %s.", resolve_resource("apps.json"))

    service = ArtCraftRPCService(config=config, apps=apps)
    headless = args.headless or config.headless

    LOGGER.info(
        "Starting ArtCraft RPC (headless=%s, refresh=%.1fs, apps=%d)",
        headless,
        config.refresh_interval,
        len(apps),
    )

    try:
        if headless:
            run_headless(service)
        else:
            run_with_tray(service, config)
    finally:
        try:
            win32api.CloseHandle(mutex)
        except Exception:
            pass

    return 0
