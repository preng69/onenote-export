from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from onenote_export.util import safe_filename, unique_path


class UtilTests(unittest.TestCase):
    def test_safe_filename_normalizes_input(self) -> None:
        self.assertEqual("Plan 2026.md", safe_filename("Plán / 2026?.md"))
        self.assertEqual("untitled", safe_filename("///"))

    def test_unique_path_appends_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "note.html"
            base.write_text("x", encoding="utf-8")
            candidate = unique_path(base)
            self.assertEqual("note (2).html", candidate.name)


if __name__ == "__main__":
    unittest.main()
