import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from onenote_export.enex import build_enex_file, enex_output_paths, render_graph_enml
from onenote_export.graph import DownloadedResource

SAMPLE_HTML = """
<html>
  <head><title>Sample</title></head>
  <body>
    <div style="position:absolute;top:10px;left:0px">
      <img src="https://graph.microsoft.com/v1.0/me/onenote/resources/1/$value" data-src-type="image/png" alt="diagram" />
    </div>
    <object data="https://graph.microsoft.com/v1.0/me/onenote/resources/2/$value" data-attachment="report.pdf" type="application/pdf"></object>
  </body>
</html>
"""


def fake_fetcher(url: str) -> DownloadedResource:
    if url.endswith("/1/$value"):
        return DownloadedResource(data=b"\x89PNG\r\n\x1a\n", mime_type="image/png")
    if url.endswith("/2/$value"):
        return DownloadedResource(data=b"%PDF-1.7", mime_type="application/pdf")
    raise AssertionError(f"Unexpected URL: {url}")


class EnexTests(unittest.TestCase):
    def test_render_enml_uses_en_media_and_hashes(self) -> None:
        rendered = render_graph_enml(page_html=SAMPLE_HTML, resource_fetcher=fake_fetcher)
        self.assertIn("<en-media", rendered.en_note_document)
        self.assertIn('hash="', rendered.en_note_document)
        self.assertIn('type="image/png"', rendered.en_note_document)
        self.assertEqual(2, len(rendered.resources))

    def test_build_enex_round_trip_xml(self) -> None:
        rendered = render_graph_enml(page_html=SAMPLE_HTML, resource_fetcher=fake_fetcher)
        xml = build_enex_file(
            pages=[(rendered, "Nb / Sec / Page", "2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z")]
        )
        root = ET.fromstring(xml)
        self.assertEqual(root.tag, "en-export")
        note = root.find("note")
        assert note is not None
        title = note.find("title")
        assert title is not None and title.text
        self.assertIn("Nb / Sec / Page", title.text)
        resources = note.findall("resource")
        self.assertEqual(2, len(resources))

    def test_enex_file_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "out.enex"
            ep, base = enex_output_paths(p)
            self.assertEqual(ep, p)
            rendered = render_graph_enml(page_html=SAMPLE_HTML, resource_fetcher=fake_fetcher)
            xml = build_enex_file(pages=[(rendered, "T", None, None)])
            ep.write_text(xml, encoding="utf-8")
            self.assertTrue(ep.exists())


if __name__ == "__main__":
    unittest.main()
