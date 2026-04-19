from __future__ import annotations

import base64
import hashlib
import html
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from bs4 import BeautifulSoup, NavigableString, Tag

from .graph import DownloadedResource
from .render_html import (
    STYLE_KEYS_TO_DROP,
    _extension_for_mime_type,
    _linearize_style,
    _marker_for_tag,
    _sort_top_level_blocks,
    _strip_graph_attrs,
)
from .util import css_px, safe_filename, style_dict, style_string


@dataclass
class EnexResource:
    hash_hex: str
    data: bytes
    mime: str
    filename: str
    width: int | None = None
    height: int | None = None


@dataclass
class RenderedEnexPage:
    en_note_document: str
    resources: list[EnexResource]
    warnings: list[str] = field(default_factory=list)

    def manifest_assets(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for r in self.resources:
            out.append(
                {
                    "kind": "resource",
                    "filename": r.filename,
                    "mime_type": r.mime,
                    "hash": r.hash_hex,
                    "bytes": len(r.data),
                }
            )
        return out


def render_graph_enml(
    *,
    page_html: str,
    resource_fetcher: Callable[[str], DownloadedResource],
    max_embed_bytes: int = 256 * 1024 * 1024,
) -> RenderedEnexPage:
    """Build ENML (Evernote Markup) with <en-media> + resource payloads (MD5 hash)."""
    soup = BeautifulSoup(page_html, "lxml")
    body = soup.body or soup
    _sort_top_level_blocks(body)

    resources: list[EnexResource] = []
    warnings: list[str] = []
    seen_hashes: set[str] = set()

    for tag in list(body.find_all(True)):
        _linearize_style(tag)
        if tag.has_attr("data-tag"):
            _apply_data_tag_enml(soup, tag)

    for image in list(body.find_all("img")):
        resource_url = image.get("data-fullres-src") or image.get("src")
        if not resource_url:
            warnings.append("Skipped an image without a source URL.")
            continue
        downloaded = resource_fetcher(resource_url)
        mime_type = image.get("data-fullres-src-type") or image.get("data-src-type") or downloaded.mime_type
        if not mime_type:
            mime_type = "application/octet-stream"
        data = downloaded.data
        if len(data) > max_embed_bytes:
            warnings.append(
                f"Skipped an image larger than max ({len(data)} bytes); not included in ENEX."
            )
            image.decompose()
            continue

        hash_hex = hashlib.md5(data).hexdigest()
        if hash_hex not in seen_hashes:
            seen_hashes.add(hash_hex)
            fname = safe_filename(image.get("alt") or "image") or "image"
            if "." not in fname and mime_type:
                ext = _extension_for_mime_type(mime_type)
                if ext:
                    fname = f"{fname}.{ext}"
            resources.append(
                EnexResource(
                    hash_hex=hash_hex,
                    data=data,
                    mime=mime_type,
                    filename=fname,
                    width=_img_width(image),
                    height=_img_height(image),
                )
            )

        media = soup.new_tag("en-media")
        media["hash"] = hash_hex
        media["type"] = mime_type
        if image.get("alt"):
            media["alt"] = str(image.get("alt"))
        styles = style_dict(image.get("style"))
        if styles:
            media["style"] = style_string(styles)
        image.replace_with(media)

    for obj in list(body.find_all("object")):
        rep = _replace_object_enex(
            soup=soup,
            tag=obj,
            resource_fetcher=resource_fetcher,
            seen_hashes=seen_hashes,
            resources=resources,
            max_embed_bytes=max_embed_bytes,
        )
        if rep["warning"]:
            warnings.append(str(rep["warning"]))

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

    _strip_enml_forbidden(body)
    for bad in list(body.find_all(["style", "script", "object", "iframe"])):
        bad.decompose()

    content_html = "".join(str(child) for child in body.children)
    en_note_document = _wrap_en_note(content_html)
    return RenderedEnexPage(
        en_note_document=en_note_document,
        resources=resources,
        warnings=warnings,
    )


def _img_width(tag: Tag) -> int | None:
    w = tag.get("width")
    if w:
        s = str(w).strip()
        if s.isdigit():
            return int(s)
        if s.replace(".", "").isdigit():
            return int(float(s))
    styles = style_dict(tag.get("style"))
    v = styles.get("width")
    if v:
        px = css_px(v)
        if px is not None:
            return int(round(px))
    return None


def _img_height(tag: Tag) -> int | None:
    h = tag.get("height")
    if h:
        s = str(h).strip()
        if s.isdigit():
            return int(s)
        if s.replace(".", "").isdigit():
            return int(float(s))
    styles = style_dict(tag.get("style"))
    v = styles.get("height")
    if v:
        px = css_px(v)
        if px is not None:
            return int(round(px))
    return None


def _apply_data_tag_enml(soup: BeautifulSoup, tag: Tag) -> None:
    raw = str(tag.get("data-tag", "")).strip().lower()
    if raw.startswith("to-do"):
        checked = "true" if "completed" in raw else "false"
        todo = soup.new_tag("en-todo", checked=checked)
        tag.insert(0, todo)
        del tag["data-tag"]
        return
    marker = _marker_for_tag(str(tag.get("data-tag")))
    if marker:
        tag.insert(0, NavigableString(marker))
    del tag["data-tag"]


def _replace_object_enex(
    *,
    soup: BeautifulSoup,
    tag: Tag,
    resource_fetcher: Callable[[str], DownloadedResource],
    seen_hashes: set[str],
    resources: list[EnexResource],
    max_embed_bytes: int,
) -> dict[str, object]:
    resource_url = tag.get("data")
    attachment_name = tag.get("data-attachment") or "attachment"
    mime_type = tag.get("type")
    if not resource_url:
        tag.decompose()
        return {"warning": "Skipped an attachment without a resource URL."}

    downloaded = resource_fetcher(resource_url)
    if not mime_type:
        mime_type = downloaded.mime_type
    if not mime_type:
        mime_type = "application/octet-stream"

    data = downloaded.data
    if len(data) > max_embed_bytes:
        tag.decompose()
        return {"warning": f"Skipped attachment {attachment_name!r} (too large for ENEX)."}

    hash_hex = hashlib.md5(data).hexdigest()
    filename = safe_filename(attachment_name)
    if "." not in filename and mime_type:
        extension = _extension_for_mime_type(mime_type)
        if extension:
            filename = f"{filename}.{extension}"

    if hash_hex not in seen_hashes:
        seen_hashes.add(hash_hex)
        resources.append(
            EnexResource(
                hash_hex=hash_hex,
                data=data,
                mime=mime_type,
                filename=filename,
            )
        )

    media = soup.new_tag("en-media")
    media["hash"] = hash_hex
    media["type"] = mime_type
    media["style"] = "display:block;margin:0.5em 0;"
    paragraph = soup.new_tag("p")
    label = soup.new_tag("strong")
    label.string = "Attachment:"
    paragraph.append(label)
    paragraph.append(" ")
    paragraph.append(media)
    paragraph.append(f" {filename}")
    tag.replace_with(paragraph)
    return {"warning": None}


def _strip_enml_forbidden(body: Tag) -> None:
    for tag in list(body.find_all(True)):
        for attr in list(tag.attrs):
            al = attr.lower()
            if al in ("id", "class"):
                del tag[attr]
                continue
            if al.startswith("on"):
                del tag[attr]
        if tag.has_attr("style"):
            styles = style_dict(tag.get("style"))
            for key in STYLE_KEYS_TO_DROP:
                styles.pop(key, None)
            if styles:
                tag["style"] = style_string(styles)
            else:
                del tag["style"]


def _wrap_en_note(content_html: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'
        f"<en-note>{content_html}</en-note>"
    )


def graph_datetime_to_enex(dt: str | None) -> str:
    if not dt:
        return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_enex_file(
    *,
    pages: list[tuple[RenderedEnexPage, str, str | None, str | None]],
    application: str = "onenote-export",
    version: str = "0.1.0",
) -> str:
    """Serialize multiple notes into one .enex document. Each tuple is (page, title, created, modified)."""
    export_date = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE en-export SYSTEM "http://xml.evernote.com/pub/evernote-export3.dtd">',
        f'<en-export export-date="{export_date}" application="{_xml_attr_escape(application)}" version="{_xml_attr_escape(version)}">',
    ]
    for rendered, title, created, modified in pages:
        lines.append("<note>")
        lines.append(f"<title>{_xml_escape_text(title)}</title>")
        inner = rendered.en_note_document
        inner = inner.replace("]]>", "]]]]><![CDATA[>")
        lines.append("<content><![CDATA[" + inner + "]]></content>")
        lines.append(f"<created>{graph_datetime_to_enex(created)}</created>")
        lines.append(f"<updated>{graph_datetime_to_enex(modified)}</updated>")
        seen_r: set[str] = set()
        for res in rendered.resources:
            if res.hash_hex in seen_r:
                continue
            seen_r.add(res.hash_hex)
            lines.extend(_serialize_resource(res))
        lines.append("</note>")
    lines.append("</en-export>")
    return "\n".join(lines) + "\n"


def _serialize_resource(res: EnexResource) -> list[str]:
    b64 = base64.b64encode(res.data).decode("ascii")
    out = [
        "<resource>",
        f"<mime>{_xml_escape_text(res.mime)}</mime>",
        f'<data encoding="base64">{b64}</data>',
    ]
    if res.width is not None:
        out.append(f"<width>{res.width}</width>")
    if res.height is not None:
        out.append(f"<height>{res.height}</height>")
    out.append("<resource-attributes>")
    out.append(f"<file-name>{_xml_escape_text(res.filename)}</file-name>")
    out.append("<source-url></source-url></resource-attributes>")
    out.append("</resource>")
    return out


def _xml_escape_text(s: str) -> str:
    return (
        html.escape(s, quote=False)
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def _xml_attr_escape(s: str) -> str:
    return html.escape(s, quote=True)


def validate_enex_wellformed(enex_xml: str) -> None:
    """Parse the file as XML (best-effort). Raises if malformed."""
    ET.fromstring(enex_xml)


def enex_output_paths(output: Path) -> tuple[Path, Path]:
    """Return (path to the .enex file, base directory for manifest and parents)."""
    if output.suffix.lower() == ".enex":
        return output, output.parent
    return output / "onenote-export.enex", output
