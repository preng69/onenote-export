# OneNote Export

Export Microsoft OneNote notebooks into local `.html` files, or into a single Evernote `.enex` file that Apple Notes imports with inline images and attachments preserved.

The exporter uses:

- `msal` for browser-based Microsoft sign-in
- `requests` for Microsoft Graph calls
- `beautifulsoup4` and `lxml` to flatten OneNote page HTML into a Notes-friendly form

## What It Exports

- notebooks, section groups, sections, and pages through Microsoft Graph
- **HTML** (default): one `.html` file per page; inline images as `data:` URLs; non-image attachments embedded the same way by default (use `--no-embed-attachments` for sidecar files under `.assets/` only)
- **ENEX** (`--format enex`): one Evernote export file; each imported note is titled with the **OneNote page title only** (notebook/section are in `manifest.json`, not in the note name). Images and files use `<en-media>` + resource payloads.
- **Single section** (`--single-section` with `--notebook` and `--section` matching one section): export only that section; HTML goes under `notes/<section groups>/<section>/` without a top-level notebook folder
- a `manifest.json` with source metadata and output paths

## Current Limits

- page layouts are linearized; exact OneNote freeform positioning is not preserved
- attachments are carried inside the HTML as `data:` links, not as true “native” Notes file attachments (generating Apple Notes’ internal database format is out of scope for this tool)
- attachments larger than `--max-embed-bytes` (default 15 MiB) stay on disk under `.assets/` with a URL-encoded relative link
- handwritten ink and some OneNote-only embeds are reduced to text or links

## Prerequisites

1. Create a Microsoft Entra app registration for a public client.
2. Supported account types:
   - choose the account type that matches your OneNote account
   - use "Accounts in any organizational directory and personal Microsoft accounts" if you want both
3. Under `Authentication`, add a `Mobile and desktop applications` redirect URI:
   - `http://localhost`
4. Under `API permissions`, add Microsoft Graph delegated permission:
   - `Notes.Read`

No client secret is required.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Usage

Sign in with the browser flow and save your client ID:

```bash
.venv/bin/onenote-export auth login --client-id YOUR_APP_CLIENT_ID
```

The default authority is `common` (multi-tenant). If your app registration is **single-tenant**, you must use your tenant instead or sign-in fails with `AADSTS50194`. Pass your **Directory (tenant) ID** from the Entra overview page (or your tenant domain, for example `contoso.onmicrosoft.com`):

```bash
.venv/bin/onenote-export auth login --client-id YOUR_APP_CLIENT_ID --authority YOUR_TENANT_ID_OR_DOMAIN
```

You can set `ONENOTE_EXPORT_AUTHORITY` to the same value if you prefer not to pass `--authority` each time. The chosen authority is saved in `~/.config/onenote-export/config.json`.

List notebooks:

```bash
.venv/bin/onenote-export list notebooks
```

Export everything as HTML:

```bash
.venv/bin/onenote-export export --output ./export
```

Export to Evernote ENEX (then use **File → Import to Notes…** in Apple Notes and choose the `.enex` file):

```bash
.venv/bin/onenote-export export --format enex --output ./export/onenote-export.enex
# or a directory (writes export/onenote-export.enex inside it):
.venv/bin/onenote-export export --format enex --output ./export
```

Export one notebook by name or ID:

```bash
.venv/bin/onenote-export export --output ./export --notebook "Personal"
```

Export **one section only** (chapter): `--notebook` and `--section` together must match **exactly one** section. HTML files go under `notes/<optional section groups>/<section>/` without a notebook folder at the top.

```bash
.venv/bin/onenote-export export --format enex --output ./chapter.enex \
  --notebook "My Notebook" --section "Chapter 3" --single-section
```

After HTML export, import the `export/notes` folder into Apple Notes. After ENEX export, import the `.enex` file.

## Commands

```bash
.venv/bin/onenote-export auth login --client-id ...
.venv/bin/onenote-export auth status
.venv/bin/onenote-export auth logout
.venv/bin/onenote-export list notebooks
.venv/bin/onenote-export list sections
.venv/bin/onenote-export list pages --notebook "Notebook Name"
.venv/bin/onenote-export export --output ./export
.venv/bin/onenote-export export --format enex --output ./notes.enex
.venv/bin/onenote-export export --single-section --notebook "Nb" --section "Sec" --format enex --output ./part.enex
```

## Local State

Config and auth cache are stored under:

- `~/.config/onenote-export/config.json`
- `~/.config/onenote-export/token_cache.bin`
- `~/.config/onenote-export/auth_state.json`

Optional environment variables:

- `ONENOTE_EXPORT_CONFIG_DIR` — override the config directory
- `ONENOTE_EXPORT_AUTHORITY` — default tenant segment when `--authority` is omitted (see Usage)

## Testing

```bash
.venv/bin/python -m unittest discover -s tests
```

