from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from onenote_export.graph import DownloadedResource
from onenote_export.render_html import render_graph_html


SAMPLE_HTML = """
<html>
  <head><title>Sample</title></head>
  <body>
    <div style="position:absolute;top:120px;left:0px;width:500px">
      <p data-tag="to-do">Finish migration</p>
    </div>
    <div style="position:absolute;top:10px;left:0px">
      <h1>Heading</h1>
      <img src="https://graph.microsoft.com/v1.0/me/onenote/resources/1/$value" data-src-type="image/png" />
    </div>
    <object data="https://graph.microsoft.com/v1.0/me/onenote/resources/2/$value" data-attachment="Quarterly report.pdf" type="application/pdf"></object>
    <iframe src="https://www.youtube.com/embed/example"></iframe>
  </body>
</html>
"""


def fake_fetcher(url: str) -> DownloadedResource:
    if url.endswith("/1/$value"):
        return DownloadedResource(data=b"\x89PNG\r\n\x1a\n", mime_type="image/png")
    if url.endswith("/2/$value"):
        return DownloadedResource(data=b"%PDF-1.7", mime_type="application/pdf")
    raise AssertionError(f"Unexpected URL: {url}")


class RenderHtmlTests(unittest.TestCase):
    def test_render_inlines_images_and_embeds_attachments_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            asset_dir = Path(tmpdir) / "Page.assets"
            rendered = render_graph_html(
                title="Page",
                page_html=SAMPLE_HTML,
                asset_dir=asset_dir,
                asset_href_prefix="Page.assets",
                resource_fetcher=fake_fetcher,
            )

            self.assertIn("data:image/png;base64,", rendered.document)
            self.assertIn("Attachment:", rendered.document)
            self.assertIn("data:application/pdf;base64,", rendered.document)
            self.assertIn("Embedded content:", rendered.document)
            self.assertIn("☐ Finish migration", rendered.document)
            self.assertFalse((asset_dir / "Quarterly report.pdf").exists())
            self.assertEqual(2, len(rendered.assets))
            self.assertTrue(rendered.assets[1].embedded)

    def test_render_saves_sidecar_when_embed_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            asset_dir = Path(tmpdir) / "Page.assets"
            rendered = render_graph_html(
                title="Page",
                page_html=SAMPLE_HTML,
                asset_dir=asset_dir,
                asset_href_prefix="Page.assets",
                resource_fetcher=fake_fetcher,
                embed_attachments=False,
            )

            self.assertIn("Page.assets/Quarterly%20report.pdf", rendered.document)
            self.assertTrue((asset_dir / "Quarterly report.pdf").exists())
            self.assertFalse(rendered.assets[1].embedded)

    def test_render_sidecar_when_attachment_exceeds_max_embed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            asset_dir = Path(tmpdir) / "Page.assets"
            rendered = render_graph_html(
                title="Page",
                page_html=SAMPLE_HTML,
                asset_dir=asset_dir,
                asset_href_prefix="Page.assets",
                resource_fetcher=fake_fetcher,
                embed_attachments=True,
                max_embed_bytes=4,
            )

            self.assertIn("Page.assets/Quarterly%20report.pdf", rendered.document)
            self.assertTrue((asset_dir / "Quarterly report.pdf").exists())
            self.assertTrue(any("exceeds --max-embed-bytes" in w for w in rendered.warnings))


if __name__ == "__main__":
    unittest.main()

