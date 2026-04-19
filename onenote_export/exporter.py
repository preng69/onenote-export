from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .enex import RenderedEnexPage, build_enex_file, enex_output_paths, render_graph_enml
from .graph import GraphClient
from .render_html import RenderedNote, render_graph_html
from .util import ensure_dir, graph_name, safe_filename, unique_path, write_json


@dataclass
class Notebook:
    id: str
    name: str


@dataclass
class SectionGroup:
    id: str
    name: str
    parent_notebook_id: str | None
    parent_section_group_id: str | None


@dataclass
class Section:
    id: str
    name: str
    parent_notebook_id: str | None
    parent_section_group_id: str | None


@dataclass
class Page:
    id: str
    title: str
    created: str | None
    modified: str | None
    order: int | None
    level: int | None
    section_id: str


@dataclass
class ExportResult:
    notes_written: int
    attachments_written: int
    output_dir: Path
    manifest_path: Path
    warnings: list[str]
    enex_path: Path | None = None


class OneNoteExporter:
    def __init__(self, graph: GraphClient):
        self.graph = graph

    def list_structure(self) -> tuple[list[Notebook], list[SectionGroup], list[Section]]:
        notebooks = [_notebook_from_api(item) for item in self.graph.list_notebooks()]
        section_groups = [_section_group_from_api(item) for item in self.graph.list_section_groups()]
        sections = [_section_from_api(item) for item in self.graph.list_sections()]
        return notebooks, section_groups, sections

    def list_pages(
        self,
        *,
        notebook_filters: set[str] | None = None,
        section_filters: set[str] | None = None,
        limit: int | None = None,
    ) -> list[tuple[Notebook, Section, Page]]:
        notebooks, section_groups, sections = self.list_structure()
        notebook_by_id = {item.id: item for item in notebooks}
        section_group_by_id = {item.id: item for item in section_groups}
        filtered_sections = [
            section
            for section in sections
            if _matches_filter(section, notebook_by_id, notebook_filters, section_filters)
        ]

        pages: list[tuple[Notebook, Section, Page]] = []
        for section in filtered_sections:
            notebook = notebook_by_id.get(section.parent_notebook_id or "")
            if not notebook:
                continue
            for raw_page in self.graph.list_pages_in_section(section.id):
                pages.append((notebook, section, _page_from_api(raw_page, section.id)))
                if limit is not None and len(pages) >= limit:
                    return pages
        return pages

    def export(
        self,
        *,
        output_dir: Path,
        notebook_filters: set[str] | None = None,
        section_filters: set[str] | None = None,
        page_ids: set[str] | None = None,
        limit: int | None = None,
        write_raw_html: bool = False,
        embed_attachments: bool = True,
        max_embed_bytes: int = 15 * 1024 * 1024,
        export_format: Literal["html", "enex"] = "html",
        single_section: bool = False,
    ) -> ExportResult:
        if export_format == "enex":
            return self._export_enex(
                output_dir=output_dir,
                notebook_filters=notebook_filters,
                section_filters=section_filters,
                page_ids=page_ids,
                limit=limit,
                max_embed_bytes=max_embed_bytes,
                single_section=single_section,
            )

        notes_root = ensure_dir(output_dir / "notes")
        raw_root = ensure_dir(output_dir / "raw-html") if write_raw_html else None
        warnings: list[str] = []

        notebooks, section_groups, sections = self.list_structure()
        notebook_by_id = {item.id: item for item in notebooks}
        section_group_by_id = {item.id: item for item in section_groups}

        manifest: dict[str, Any] = {
            "exportedAt": datetime.now(tz=timezone.utc).isoformat(),
            "notesRoot": str(notes_root),
            "notes": [],
            "warnings": warnings,
        }
        if single_section:
            manifest["singleSection"] = True

        notes_written = 0
        attachments_written = 0

        if single_section:
            _, section = _resolve_single_section(
                notebook_by_id=notebook_by_id,
                sections=sections,
                notebook_filters=notebook_filters,
                section_filters=section_filters,
            )
            section_iter: list[Section] = [section]
        else:
            section_iter = list(sections)

        for section in section_iter:
            if not single_section and not _matches_filter(
                section, notebook_by_id, notebook_filters, section_filters
            ):
                continue

            notebook = notebook_by_id.get(section.parent_notebook_id or "")
            if not notebook:
                continue

            if single_section:
                section_path_parts = list(_section_group_path(section.parent_section_group_id, section_group_by_id))
                section_path_parts.append(_path_part(section.name))
            else:
                section_path_parts = [_path_part(notebook.name)]
                section_path_parts.extend(_section_group_path(section.parent_section_group_id, section_group_by_id))
                section_path_parts.append(_path_part(section.name))
            section_dir = ensure_dir(notes_root.joinpath(*section_path_parts))

            for raw_page in self.graph.list_pages_in_section(section.id):
                page = _page_from_api(raw_page, section.id)
                if page_ids and page.id not in page_ids:
                    continue
                title_part = _page_filename(page)
                note_path = unique_path(section_dir / f"{title_part}.html")
                asset_dir = note_path.with_suffix(".assets")
                page_html = self.graph.get_page_content(page.id)
                rendered = self._render_page(
                    page=page,
                    note_path=note_path,
                    asset_dir=asset_dir,
                    page_html=page_html,
                    embed_attachments=embed_attachments,
                    max_embed_bytes=max_embed_bytes,
                )

                note_path.write_text(rendered.document, encoding="utf-8")
                if write_raw_html and raw_root is not None:
                    raw_path = unique_path(raw_root / f"{safe_filename(page.title)}.raw.html")
                    raw_path.write_text(page_html, encoding="utf-8")

                notes_written += 1
                attachments_written += sum(1 for asset in rendered.assets if asset.kind == "attachment")
                warnings.extend(rendered.warnings)
                manifest["notes"].append(
                    {
                        "pageId": page.id,
                        "title": page.title,
                        "created": page.created,
                        "modified": page.modified,
                        "order": page.order,
                        "level": page.level,
                        "notebook": notebook.name,
                        "section": section.name,
                        "relativePath": str(note_path.relative_to(output_dir)),
                        "assets": rendered.manifest_assets(),
                    }
                )
                if limit is not None and notes_written >= limit:
                    manifest_path = output_dir / "manifest.json"
                    write_json(manifest_path, manifest)
                    return ExportResult(notes_written, attachments_written, output_dir, manifest_path, warnings)

        manifest_path = output_dir / "manifest.json"
        write_json(manifest_path, manifest)
        return ExportResult(notes_written, attachments_written, output_dir, manifest_path, warnings)

    def _export_enex(
        self,
        *,
        output_dir: Path,
        notebook_filters: set[str] | None = None,
        section_filters: set[str] | None = None,
        page_ids: set[str] | None = None,
        limit: int | None = None,
        max_embed_bytes: int = 15 * 1024 * 1024,
        single_section: bool = False,
    ) -> ExportResult:
        enex_path, base_dir = enex_output_paths(output_dir)
        ensure_dir(base_dir)
        warnings: list[str] = []

        notebooks, _, sections = self.list_structure()
        notebook_by_id = {item.id: item for item in notebooks}

        manifest: dict[str, Any] = {
            "exportedAt": datetime.now(tz=timezone.utc).isoformat(),
            "format": "enex",
            "enexPath": str(enex_path.resolve()),
            "notes": [],
            "warnings": warnings,
        }
        if single_section:
            manifest["singleSection"] = True

        notes_written = 0
        attachments_written = 0
        page_entries: list[tuple[RenderedEnexPage, str, str | None, str | None]] = []

        if single_section:
            notebook, only_section = _resolve_single_section(
                notebook_by_id=notebook_by_id,
                sections=sections,
                notebook_filters=notebook_filters,
                section_filters=section_filters,
            )
            section_notebook_pairs: list[tuple[Notebook, Section]] = [(notebook, only_section)]
        else:
            section_notebook_pairs = []
            for section in sections:
                if not _matches_filter(section, notebook_by_id, notebook_filters, section_filters):
                    continue
                notebook = notebook_by_id.get(section.parent_notebook_id or "")
                if not notebook:
                    continue
                section_notebook_pairs.append((notebook, section))

        for notebook, section in section_notebook_pairs:
            for raw_page in self.graph.list_pages_in_section(section.id):
                page = _page_from_api(raw_page, section.id)
                if page_ids and page.id not in page_ids:
                    continue
                page_html = self.graph.get_page_content(page.id)
                rendered = render_graph_enml(
                    page_html=page_html,
                    resource_fetcher=self.graph.download_resource,
                    max_embed_bytes=max_embed_bytes,
                )
                title = page.title
                page_entries.append((rendered, title, page.created, page.modified))
                notes_written += 1
                attachments_written += len(rendered.resources)
                warnings.extend(rendered.warnings)
                manifest["notes"].append(
                    {
                        "pageId": page.id,
                        "title": page.title,
                        "enexTitle": title,
                        "created": page.created,
                        "modified": page.modified,
                        "order": page.order,
                        "level": page.level,
                        "notebook": notebook.name,
                        "section": section.name,
                        "assets": rendered.manifest_assets(),
                    }
                )
                if limit is not None and notes_written >= limit:
                    break
            if limit is not None and notes_written >= limit:
                break

        enex_xml = build_enex_file(pages=page_entries)
        ensure_dir(enex_path.parent)
        enex_path.write_text(enex_xml, encoding="utf-8")
        manifest_path = base_dir / "manifest.json"
        write_json(manifest_path, manifest)
        return ExportResult(
            notes_written,
            attachments_written,
            base_dir,
            manifest_path,
            warnings,
            enex_path=enex_path,
        )

    def _render_page(
        self,
        *,
        page: Page,
        note_path: Path,
        asset_dir: Path,
        page_html: str,
        embed_attachments: bool,
        max_embed_bytes: int,
    ) -> RenderedNote:
        rendered = render_graph_html(
            title=page.title,
            page_html=page_html,
            asset_dir=asset_dir,
            asset_href_prefix=asset_dir.name,
            resource_fetcher=self.graph.download_resource,
            embed_attachments=embed_attachments,
            max_embed_bytes=max_embed_bytes,
        )
        return rendered


def _notebook_from_api(item: dict[str, Any]) -> Notebook:
    return Notebook(id=str(item["id"]), name=graph_name(item))


def _section_group_from_api(item: dict[str, Any]) -> SectionGroup:
    parent_notebook = item.get("parentNotebook") or {}
    parent_section_group = item.get("parentSectionGroup") or {}
    return SectionGroup(
        id=str(item["id"]),
        name=graph_name(item),
        parent_notebook_id=_string_or_none(parent_notebook.get("id")),
        parent_section_group_id=_string_or_none(parent_section_group.get("id")),
    )


def _section_from_api(item: dict[str, Any]) -> Section:
    parent_notebook = item.get("parentNotebook") or {}
    parent_section_group = item.get("parentSectionGroup") or {}
    return Section(
        id=str(item["id"]),
        name=graph_name(item),
        parent_notebook_id=_string_or_none(parent_notebook.get("id")),
        parent_section_group_id=_string_or_none(parent_section_group.get("id")),
    )


def _page_from_api(item: dict[str, Any], section_id: str) -> Page:
    return Page(
        id=str(item["id"]),
        title=str(item.get("title") or "Untitled"),
        created=_string_or_none(item.get("createdDateTime")),
        modified=_string_or_none(item.get("lastModifiedDateTime")),
        order=_int_or_none(item.get("order")),
        level=_int_or_none(item.get("level")),
        section_id=section_id,
    )


def _matches_filter(
    section: Section,
    notebook_by_id: dict[str, Notebook],
    notebook_filters: set[str] | None,
    section_filters: set[str] | None,
) -> bool:
    notebook = notebook_by_id.get(section.parent_notebook_id or "")
    if notebook_filters:
        notebook_candidates = {
            (section.parent_notebook_id or "").casefold(),
            (notebook.name if notebook else "").casefold(),
        }
        if not notebook_candidates & notebook_filters:
            return False
    if section_filters:
        section_candidates = {section.id.casefold(), section.name.casefold()}
        if not section_candidates & section_filters:
            return False
    return True


def _matching_section_pairs(
    notebook_by_id: dict[str, Notebook],
    sections: list[Section],
    notebook_filters: set[str] | None,
    section_filters: set[str] | None,
) -> list[tuple[Notebook, Section]]:
    pairs: list[tuple[Notebook, Section]] = []
    for section in sections:
        if not _matches_filter(section, notebook_by_id, notebook_filters, section_filters):
            continue
        notebook = notebook_by_id.get(section.parent_notebook_id or "")
        if not notebook:
            continue
        pairs.append((notebook, section))
    return pairs


def _resolve_single_section(
    *,
    notebook_by_id: dict[str, Notebook],
    sections: list[Section],
    notebook_filters: set[str] | None,
    section_filters: set[str] | None,
) -> tuple[Notebook, Section]:
    pairs = _matching_section_pairs(notebook_by_id, sections, notebook_filters, section_filters)
    if not pairs:
        raise RuntimeError(
            "Single-section export: no section matched your filters. "
            "Refine --notebook and --section (name or id)."
        )
    if len(pairs) > 1:
        sample = [f"{nb.name} / {sec.name}" for nb, sec in pairs[:12]]
        extra = "" if len(pairs) <= 12 else f" (+{len(pairs) - 12} more)"
        raise RuntimeError(
            "Single-section export needs exactly one matching section; "
            f"found {len(pairs)}: {', '.join(sample)}{extra}. "
            "Narrow --notebook and --section until only one section matches."
        )
    return pairs[0]


def _section_group_path(
    section_group_id: str | None, section_group_by_id: dict[str, SectionGroup]
) -> list[str]:
    if not section_group_id:
        return []
    parts: list[str] = []
    seen: set[str] = set()
    current = section_group_id
    while current and current not in seen:
        seen.add(current)
        group = section_group_by_id.get(current)
        if not group:
            break
        parts.append(_path_part(group.name))
        current = group.parent_section_group_id
    return list(reversed(parts))


def _path_part(value: str) -> str:
    return safe_filename(value) or "untitled"


def _page_filename(page: Page) -> str:
    prefix = ""
    if page.order is not None:
        prefix = f"{page.order:03d} "
    level_marker = ""
    if page.level:
        level_marker = f"{'-' * page.level} "
    return safe_filename(f"{prefix}{level_marker}{page.title}")


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
