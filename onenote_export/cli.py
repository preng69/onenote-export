from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .auth import AuthError, AuthManager
from .config import AppConfig, load_config, require_config, save_config
from .exporter import OneNoteExporter
from .graph import GraphClient, GraphError


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except (AuthError, GraphError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onenote-export")
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_parser = subparsers.add_parser("auth", help="Authenticate with Microsoft")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", required=True)

    auth_login = auth_subparsers.add_parser("login", help="Sign in with the browser flow")
    auth_login.add_argument("--client-id", help="Microsoft Entra app client ID")
    auth_login.add_argument(
        "--authority",
        help=(
            "Authority segment for login.microsoftonline.com/<value>. "
            "Use 'common' for multi-tenant apps (default). "
            "For single-tenant apps, use your directory (tenant) ID or "
            "tenant domain (e.g. contoso.onmicrosoft.com)."
        ),
    )
    auth_login.add_argument("--port", type=int, help="Local browser callback port; default 8400")
    auth_login.add_argument("--login-hint", help="Optional username/email hint")
    auth_login.set_defaults(func=cmd_auth_login)

    auth_status = auth_subparsers.add_parser("status", help="Show cached auth status")
    auth_status.set_defaults(func=cmd_auth_status)

    auth_logout = auth_subparsers.add_parser("logout", help="Clear the local token cache")
    auth_logout.set_defaults(func=cmd_auth_logout)

    list_parser = subparsers.add_parser("list", help="List notebooks, sections, or pages")
    list_subparsers = list_parser.add_subparsers(dest="list_command", required=True)

    list_notebooks = list_subparsers.add_parser("notebooks", help="List notebooks")
    list_notebooks.set_defaults(func=cmd_list_notebooks)

    list_sections = list_subparsers.add_parser("sections", help="List sections")
    list_sections.add_argument("--notebook", action="append", default=[], help="Notebook name or ID filter")
    list_sections.set_defaults(func=cmd_list_sections)

    list_pages = list_subparsers.add_parser("pages", help="List pages")
    list_pages.add_argument("--notebook", action="append", default=[], help="Notebook name or ID filter")
    list_pages.add_argument("--section", action="append", default=[], help="Section name or ID filter")
    list_pages.add_argument("--limit", type=int, help="Maximum number of pages to list")
    list_pages.set_defaults(func=cmd_list_pages)

    export_parser = subparsers.add_parser("export", help="Export OneNote pages to HTML or Evernote ENEX")
    export_parser.add_argument(
        "--format",
        dest="export_format",
        choices=["html", "enex"],
        default="html",
        help='Output format: HTML tree under notes/ (default), or one Evernote ".enex" file (imports well into Apple Notes).',
    )
    export_parser.add_argument("--output", type=Path, default=Path("export"), help="Output directory, or path ending with .enex when using --format enex")
    export_parser.add_argument("--notebook", action="append", default=[], help="Notebook name or ID filter")
    export_parser.add_argument("--section", action="append", default=[], help="Section name or ID filter")
    export_parser.add_argument(
        "--single-section",
        action="store_true",
        help=(
            "Export only one section: --notebook/--section must match exactly one section. "
            "HTML output goes under notes/<section-group folders>/<section>/ without a top-level notebook folder."
        ),
    )
    export_parser.add_argument("--page-id", action="append", default=[], help="Explicit page ID filter")
    export_parser.add_argument("--limit", type=int, help="Maximum number of notes to export")
    export_parser.add_argument("--write-raw-html", action="store_true", help="Store the raw Graph page HTML for debugging")
    export_parser.add_argument(
        "--embed-attachments",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Embed non-image attachments as data: URLs in the HTML so Apple Notes import keeps links working "
            "(default: on). Use --no-embed-attachments to write files under .assets/ only."
        ),
    )
    export_parser.add_argument(
        "--max-embed-bytes",
        type=int,
        default=15 * 1024 * 1024,
        metavar="N",
        help="Largest attachment to embed; larger files are saved as sidecar files (default: 15 MiB).",
    )
    export_parser.set_defaults(func=cmd_export)

    return parser


def cmd_auth_login(args: argparse.Namespace) -> int:
    config = _merged_config(args)
    save_config(config)

    auth = AuthManager(config)
    result = auth.login_interactive(login_hint=args.login_hint)
    print("Login succeeded.")
    if result.username:
        print(f"Signed in as: {result.username}")
    print(f"Saved config: client_id={config.client_id} authority={config.authority} port={config.port}")
    return 0


def cmd_auth_status(_: argparse.Namespace) -> int:
    config = require_config()
    auth = AuthManager(config)
    print(json.dumps(auth.status(), indent=2))
    return 0


def cmd_auth_logout(_: argparse.Namespace) -> int:
    config = require_config()
    auth = AuthManager(config)
    auth.logout()
    print("Cleared the local auth cache.")
    return 0


def cmd_list_notebooks(_: argparse.Namespace) -> int:
    with _graph_client() as client:
        exporter = OneNoteExporter(client)
        notebooks, _, _ = exporter.list_structure()
    for notebook in notebooks:
        print(f"{notebook.name}\t{notebook.id}")
    return 0


def cmd_list_sections(args: argparse.Namespace) -> int:
    notebook_filters = _normalized_filters(args.notebook)
    with _graph_client() as client:
        exporter = OneNoteExporter(client)
        notebooks, section_groups, sections = exporter.list_structure()

    notebook_by_id = {item.id: item for item in notebooks}
    section_group_by_id = {item.id: item for item in section_groups}
    for section in sections:
        notebook = notebook_by_id.get(section.parent_notebook_id or "")
        if notebook_filters and not {
            (section.parent_notebook_id or "").casefold(),
            (notebook.name if notebook else "").casefold(),
        } & notebook_filters:
            continue
        chain = []
        current = section.parent_section_group_id
        while current:
            group = section_group_by_id.get(current)
            if not group:
                break
            chain.append(group.name)
            current = group.parent_section_group_id
        chain.reverse()
        path = " / ".join([notebook.name if notebook else "Unknown", *chain, section.name])
        print(f"{path}\t{section.id}")
    return 0


def cmd_list_pages(args: argparse.Namespace) -> int:
    notebook_filters = _normalized_filters(args.notebook)
    section_filters = _normalized_filters(args.section)
    with _graph_client() as client:
        exporter = OneNoteExporter(client)
        pages = exporter.list_pages(
            notebook_filters=notebook_filters,
            section_filters=section_filters,
            limit=args.limit,
        )
    for notebook, section, page in pages:
        print(
            f"{notebook.name} / {section.name} / {page.title}\t"
            f"page_id={page.id}\tsection_id={section.id}\tlevel={page.level}\torder={page.order}"
        )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    notebook_filters = _normalized_filters(args.notebook)
    section_filters = _normalized_filters(args.section)
    page_ids = set(args.page_id or []) or None
    output_dir: Path = args.output
    if args.export_format == "enex":
        if output_dir.suffix.lower() == ".enex":
            output_dir.parent.mkdir(parents=True, exist_ok=True)
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    with _graph_client() as client:
        exporter = OneNoteExporter(client)
        result = exporter.export(
            output_dir=output_dir,
            notebook_filters=notebook_filters,
            section_filters=section_filters,
            page_ids=page_ids,
            limit=args.limit,
            write_raw_html=args.write_raw_html and args.export_format == "html",
            embed_attachments=args.embed_attachments,
            max_embed_bytes=args.max_embed_bytes,
            export_format=args.export_format,
            single_section=args.single_section,
        )

    if result.enex_path:
        print(f"Wrote Evernote ENEX ({result.notes_written} notes): {result.enex_path}")
    else:
        print(f"Exported {result.notes_written} notes to {result.output_dir / 'notes'}")
    print(f"Downloaded {result.attachments_written} attachments")
    print(f"Manifest: {result.manifest_path}")
    if result.warnings:
        print(f"Warnings: {len(result.warnings)}")
    return 0


def _merged_config(args: argparse.Namespace) -> AppConfig:
    saved = load_config()
    client_id = args.client_id or (saved.client_id if saved else None)
    if not client_id:
        raise RuntimeError("Provide --client-id the first time you log in.")
    env_authority = os.environ.get("ONENOTE_EXPORT_AUTHORITY", "").strip()
    if args.authority:
        authority = args.authority
    elif env_authority:
        authority = env_authority
    elif saved:
        authority = saved.authority
    else:
        authority = "common"
    port = args.port or (saved.port if saved else 8400)
    scopes = saved.scopes if saved else ["Notes.Read"]
    return AppConfig(client_id=client_id, authority=authority, scopes=scopes, port=port)


def _normalized_filters(values: list[str]) -> set[str] | None:
    normalized = {value.casefold() for value in values if value}
    return normalized or None


class _GraphClientContext:
    def __init__(self) -> None:
        self.client = GraphClient(AuthManager(require_config()))

    def __enter__(self) -> GraphClient:
        return self.client

    def __exit__(self, exc_type, exc, tb) -> None:
        self.client.close()


def _graph_client() -> _GraphClientContext:
    return _GraphClientContext()

