"""Console entry point for ArtCraft Discord Rich Presence (supports --debug / --headless)."""

from __future__ import annotations

from artcraft_rpc import run_cli

if __name__ == "__main__":
    raise SystemExit(run_cli())
