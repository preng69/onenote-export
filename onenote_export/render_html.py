from __future__ import annotations

import base64
import html
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from bs4 import BeautifulSoup, NavigableString, Tag

from .graph import DownloadedResource
from .util import css_px, ensure_dir, safe_filename, style_dict, style_string, unique_path


GRAPH_ONLY_ATTRIBUTES = {
    "data-fullres-src",
    "data-fullres-src-type",
    "data-src-type",
    "data-attachment",
    "data-id",
    "data-render-src",
    "data-render-original-src",
    "data-index",
    "data-options",
    "data-tag",
}

STYLE_KEYS_TO_DROP = {"position", "top", "left", "z-index", "overflow", "transform"}


@dataclass
class ExportedAsset:
    kind: str
    filename: str
    mime_type: str | None
    relative_path: str | None
    bytes: int
    embedded: bool = False


@dataclass
class RenderedNote:
    document: str
    assets: list[ExportedAsset]
    warnings: list[str]

    def manifest_assets(self) -> list[dict[str, object]]:
        return [asdict(asset) for asset in self.assets]


def render_graph_html(
    *,
    title: str,
    page_html: str,
    asset_dir: Path,
    asset_href_prefix: str,
    resource_fetcher: Callable[[str], DownloadedResource],
    embed_attachments: bool = True,
    max_embed_bytes: int = 15 * 1024 * 1024,
) -> RenderedNote:
    soup = BeautifulSoup(page_html, "lxml")
    body = soup.body or soup
    _sort_top_level_blocks(body)

    assets: list[ExportedAsset] = []
    warnings: list[str] = []

    for tag in list(body.find_all(True)):
        _linearize_style(tag)
        if tag.has_attr("data-tag"):
            _prefix_onenote_tag(tag)

    for image in list(body.find_all("img")):
        resource_url = image.get("data-fullres-src") or image.get("src")
        if not resource_url:
            warnings.append("Skipped an image without a source URL.")
            continue
        downloaded = resource_fetcher(resource_url)
        mime_type = image.get("data-fullres-src-type") or image.get("data-src-type") or downloaded.mime_type
        if not mime_type:
            mime_type = "application/octet-stream"
        image["src"] = _data_uri(downloaded.data, mime_type)
        image["alt"] = image.get("alt", "")
        assets.append(
            ExportedAsset(
                kind="image",
                filename=safe_filename(image.get("alt") or "image"),
                mime_type=mime_type,
                relative_path=None,
                bytes=len(downloaded.data),
            )
        )
        _strip_graph_attrs(image)
        image.attrs.pop("srcset", None)

    for obj in list(body.find_all("object")):
        replacement = _replace_object_with_link(
            soup=soup,
            tag=obj,
            asset_dir=asset_dir,
            asset_href_prefix=asset_href_prefix,
            resource_fetcher=resource_fetcher,
            embed_attachments=embed_attachments,
            max_embed_bytes=max_embed_bytes,
        )
        assets.extend(replacement["assets"])
        if replacement["warning"]:
            warnings.append(replacement["warning"])

    for iframe in list(body.find_all("iframe")):
        src = iframe.get("data-original-src") or iframe.get("src")
        replacement = soup.new_tag("p")
        replacement.append("Embedded content: ")
        if src:
            link = soup.new_tag("a", href=src)
            link.string = src
            replacement.append(link)
        else:
            replacement.append("Unavailable")
        iframe.replace_with(replacement)

    for tag in list(body.find_all(True)):
        _strip_graph_attrs(tag)

    content_html = "".join(str(child) for child in body.children)
    document = _wrap_html_document(title=title, content_html=content_html)
    return RenderedNote(document=document, assets=assets, warnings=warnings)


def _replace_object_with_link(
    *,
    soup: BeautifulSoup,
    tag: Tag,
    asset_dir: Path,
    asset_href_prefix: str,
    resource_fetcher: Callable[[str], DownloadedResource],
    embed_attachments: bool,
    max_embed_bytes: int,
) -> dict[str, object]:
    resource_url = tag.get("data")
    attachment_name = tag.get("data-attachment") or "attachment"
    mime_type = tag.get("type")
    if not resource_url:
        tag.decompose()
        return {"assets": [], "warning": "Skipped an attachment without a resource URL."}

    downloaded = resource_fetcher(resource_url)
    if not mime_type:
        mime_type = downloaded.mime_type
    if not mime_type:
        mime_type = "application/octet-stream"

    data = downloaded.data
    size = len(data)
    use_embed = embed_attachments and size <= max_embed_bytes
    warning: str | None = None
    if embed_attachments and size > max_embed_bytes:
        warning = (
            f"Attachment exceeds --max-embed-bytes ({size} bytes); saved as a sidecar file instead."
        )

    filename = safe_filename(attachment_name)
    if "." not in filename and mime_type:
        extension = _extension_for_mime_type(mime_type)
        if extension:
            filename = f"{filename}.{extension}"

    paragraph = soup.new_tag("p")
    label = soup.new_tag("strong")
    label.string = "Attachment:"
    paragraph.append(label)
    paragraph.append(" ")

    if use_embed:
        href = _data_uri(data, mime_type)
        link = soup.new_tag("a", href=href)
        link.string = filename
        paragraph.append(link)
        paragraph.append(f" ({mime_type})")
        tag.replace_with(paragraph)
        return {
            "assets": [
                ExportedAsset(
                    kind="attachment",
                    filename=filename,
                    mime_type=mime_type,
                    relative_path=None,
                    bytes=size,
                    embedded=True,
                )
            ],
            "warning": warning,
        }

    ensure_dir(asset_dir)
    path = unique_path(asset_dir / filename)
    path.write_bytes(data)
    href = _quoted_asset_href(asset_href_prefix, path.name)
    link = soup.new_tag("a", href=href)
    link.string = path.name
    paragraph.append(link)
    paragraph.append(f" ({mime_type})")
    tag.replace_with(paragraph)

    return {
        "assets": [
            ExportedAsset(
                kind="attachment",
                filename=path.name,
                mime_type=mime_type,
                relative_path=href,
                bytes=size,
                embedded=False,
            )
        ],
        "warning": warning,
    }


def _wrap_html_document(*, title: str, content_html: str) -> str:
    escaped_title = html.escape(BeautifulSoup(title, "lxml").text, quote=True)
    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "  <head>\n"
        '    <meta charset="utf-8">\n'
        f"    <title>{escaped_title}</title>\n"
        "    <style>\n"
        "      body { font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', sans-serif; line-height: 1.45; }\n"
        "      img { max-width: 100%; height: auto; }\n"
        "      table { border-collapse: collapse; }\n"
        "      th, td { border: 1px solid #d0d0d0; padding: 0.35rem 0.5rem; vertical-align: top; }\n"
        "      pre { white-space: pre-wrap; }\n"
        "    </style>\n"
        "  </head>\n"
        f"  <body>{content_html}</body>\n"
        "</html>\n"
    )


def _sort_top_level_blocks(body: Tag) -> None:
    element_children = [child for child in body.children if isinstance(child, Tag)]
    if not element_children:
        return

    indexed_children = list(enumerate(element_children))
    sorted_children = sorted(
        indexed_children,
        key=lambda pair: _position_key(pair[1], pair[0]),
    )
    for child in element_children:
        child.extract()
    for _, child in sorted_children:
        body.append(child)


def _position_key(tag: Tag, fallback_index: int) -> tuple[float, float, int]:
    styles = style_dict(tag.get("style"))
    top = css_px(styles.get("top"))
    left = css_px(styles.get("left"))
    if top is None or left is None:
        return (10_000_000.0, 10_000_000.0, fallback_index)
    return (top, left, fallback_index)


def _linearize_style(tag: Tag) -> None:
    styles = style_dict(tag.get("style"))
    for key in STYLE_KEYS_TO_DROP:
        styles.pop(key, None)
    if tag.name == "img":
        styles.setdefault("max-width", "100%")
        styles.setdefault("height", "auto")
    if styles:
        tag["style"] = style_string(styles)
    elif tag.has_attr("style"):
        del tag["style"]


def _prefix_onenote_tag(tag: Tag) -> None:
    marker = _marker_for_tag(str(tag.get("data-tag")))
    if not marker:
        return
    tag.insert(0, NavigableString(marker))


def _marker_for_tag(value: str) -> str | None:
    normalized = value.strip().lower()
    mapping = {
        "to-do": "☐ ",
        "to-do:completed": "☑ ",
        "important": "[important] ",
        "question": "[question] ",
        "contact": "[contact] ",
        "address": "[address] ",
        "phone-number": "[phone] ",
    }
    return mapping.get(normalized, f"[{normalized}] " if normalized else None)


def _strip_graph_attrs(tag: Tag) -> None:
    for attr in list(tag.attrs):
        if attr in GRAPH_ONLY_ATTRIBUTES:
            del tag[attr]


def _data_uri(payload: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _quoted_asset_href(asset_href_prefix: str, filename: str) -> str:
    """Percent-encode each path segment so spaces and special characters resolve as URLs."""
    encoded_prefix = "/".join(quote(segment, safe="") for segment in asset_href_prefix.split("/"))
    return f"{encoded_prefix}/{quote(filename, safe='')}"


def _extension_for_mime_type(mime_type: str) -> str | None:
    mapping = {
        "application/pdf": "pdf",
        "text/plain": "txt",
        "application/zip": "zip",
        "image/png": "png",
        "image/jpeg": "jpg",
    }
    return mapping.get(mime_type.lower())
