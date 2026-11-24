"""Unit tests for ArtCraft Discord Rich Presence core logic."""

from __future__ import annotations

import unittest
from pathlib import Path

from artcraft_rpc import (
    _clamp_discord_text,
    _format_open_file_label,
    _is_ignored_open_file,
    build_presence_payload,
    extract_filename,
    load_apps,
    load_config,
)


class ArtCraftRPCTests(unittest.TestCase):
    """Verify configuration loading, title parsing, and payload generation."""

    def setUp(self) -> None:
        self.config = load_config()
        self.apps = load_apps()

    def test_all_expected_apps_loaded(self) -> None:
        expected = {
            "pdfcraft.exe",
            "effectcraft.exe",
            "figcraft.exe",
            "designcraft.exe",
            "filmcraft.exe",
            "lightcraft.exe",
            "materialcraft.exe",
            "photocraft.exe",
            "soundcraft.exe",
            "texturecraft.exe",
            "vectorcraft.exe",
        }
        self.assertEqual(set(self.apps.keys()), expected)

    def test_extract_filename_matches_em_dash_and_case_variants(self) -> None:
        self.assertEqual(
            extract_filename(self.apps["pdfcraft.exe"], "TP5.pdf — PdfCraft"),
            "TP5.pdf",
        )
        self.assertEqual(
            extract_filename(self.apps["photocraft.exe"], "artcraft_logo.png — PhotoCraft"),
            "artcraft_logo.png",
        )
        self.assertEqual(
            extract_filename(self.apps["lightcraft.exe"], "CeL — LightCraft"),
            "CeL",
        )
        self.assertEqual(
            extract_filename(self.apps["vectorcraft.exe"], "*logo.vc - VectorCraft"),
            "logo.vc",
        )

    def test_extract_filename_returns_none_for_idle_titles(self) -> None:
        for app in self.apps.values():
            self.assertIsNone(extract_filename(app, app.app_name))
            self.assertIsNone(extract_filename(app, "Home"))
            self.assertIsNone(extract_filename(app, ""))

    def test_build_presence_payload_modes(self) -> None:
        pdf_app = self.apps["pdfcraft.exe"]

        active = build_presence_payload(pdf_app, "TP5.pdf — PdfCraft", 1700000000, self.config, False)
        self.assertEqual(active["details"], "PDFCraft")
        self.assertEqual(active["state"], "Editing TP5.pdf")
        self.assertEqual(active["start"], 1700000000)
        self.assertEqual(active["large_image"], "pdfcraft")
        self.assertEqual(active["small_image"], self.config.small_image)

        idle = build_presence_payload(pdf_app, "PdfCraft", 1700000000, self.config, False)
        self.assertEqual(idle["state"], "Idling")

        private = build_presence_payload(pdf_app, "TP5.pdf — PdfCraft", 1700000000, self.config, True)
        self.assertEqual(private["state"], "Working on a file")

    def test_open_file_parent_dir_mode_and_filters(self) -> None:
        film_app = self.apps["filmcraft.exe"]
        self.assertEqual(film_app.open_file_mode, "parent_dir")

        self.assertEqual(
            _format_open_file_label(
                Path(r"C:\Users\julie\Videos\recette 1 graillut\DJI_0089.MP4"),
                film_app.open_file_mode,
            ),
            "recette 1 graillut",
        )
        self.assertEqual(
            _format_open_file_label(
                Path(r"C:\Users\julie\Videos\clip.mp4"),
                film_app.open_file_mode,
            ),
            "clip.mp4",
        )

        exe_dir = "c:\\program files\\artcraft\\filmcraft\\"
        self.assertTrue(
            _is_ignored_open_file(
                r"C:\Program Files\ArtCraft\FilmCraft\sample.mp4",
                exe_dir,
            )
        )
        self.assertTrue(
            _is_ignored_open_file(
                r"C:\Users\julie\AppData\Local\Temp\cache.mp4",
                exe_dir,
            )
        )
        self.assertFalse(
            _is_ignored_open_file(
                r"C:\Users\julie\Videos\recette 1 graillut\DJI_0089.MP4",
                exe_dir,
            )
        )

    def test_discord_text_clamping(self) -> None:
        self.assertEqual(_clamp_discord_text("A"), "A ")
        long_text = "x" * 200
        clamped = _clamp_discord_text(long_text)
        self.assertEqual(len(clamped), 128)
        self.assertTrue(clamped.endswith("..."))


if __name__ == "__main__":
    unittest.main()
