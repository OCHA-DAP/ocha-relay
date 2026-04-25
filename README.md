# ocha-relay

Internal utilities for the OCHA data science team's automated tools and communications.

## Status

Early-stage package. **Listmonk** (campaigns, subscribers, rendered-HTML preview, interactive safe-send) is implemented and validated end-to-end against the OCHA Listmonk demo instance. **SMTP + Jinja** helpers are planned but not yet implemented.

## Install

For local development of this package:

```bash
uv sync
```

### Installing as a dependency in another project

Until this package is published on PyPI, install directly from Git in any uv-managed project:

```bash
# Pin to a release tag (recommended — immutable ref):
uv add "ocha-relay @ git+https://github.com/OCHA-DAP/ocha-relay.git@v0.1.0"

# Pin to main (moves with the default branch):
uv add "ocha-relay @ git+https://github.com/OCHA-DAP/ocha-relay.git@main"

# Or for local editable use during development:
uv add --editable path/to/ocha-relay
```

This lands in the consumer's `pyproject.toml` as:

```toml
dependencies = ["ocha-relay"]

[tool.uv.sources]
ocha-relay = { git = "https://github.com/OCHA-DAP/ocha-relay.git", rev = "v0.1.0" }
```

(Older uv versions inlined the Git URL directly into `dependencies` using the `"ocha-relay @ git+https://..."` form. Either shape installs the same package; current uv writes the `[tool.uv.sources]` layout above.)

Consumers then import as:

```python
from ocha_relay.listmonk import ListmonkClient
```

Once the package stabilizes and the SMTP/Jinja module lands, publishing to PyPI will enable the frictionless `uv add ocha-relay`.

## Environment variables

The Listmonk client reads three variables. Copy `.env.example` as a starting point.

| Variable | Required | Notes |
|---|---|---|
| `DSCI_LISTMONK_BASE_URL` | yes | Full base URL including `/api`, no trailing slash. Trailing slash is stripped if you include one. |
| `DSCI_LISTMONK_API_USERNAME` | yes | API user configured in Listmonk admin → Settings → Users |
| `DSCI_LISTMONK_API_KEY` | yes | API key paired with the username above |

`ListmonkClient.from_env()` raises `RuntimeError` at construction time if any are missing — no silent 401s.

## Quick start

End-to-end flow: construct → create draft → inspect recipients → preview rendered HTML → send with confirmation prompt.

```python
from ocha_relay.listmonk import ListmonkClient

client = ListmonkClient.from_env()

# 1. Create a draft campaign. Does NOT send.
cid = client.create_campaign(
    name="Weekly update 2026-W17",
    subject="Weekly update",
    body="<h1>Hello</h1><p>Body copy.</p>",
    list_ids=[5, 7],
)

# 2. Inspect who would receive it.
recipients = client.campaign_recipients(cid)
print(f"{len(recipients)} people on {client.get_campaign(cid)['lists']}")

# 3. See the rendered email (body wrapped in Listmonk's template).
client.preview_in_browser(cid)   # opens a browser tab

# 4. Send. By default, prompts you to retype the campaign name to confirm.
#    In a headless environment (no stdin), this raises EOFError rather
#    than sending silently.
client.send_campaign(cid)
```

## API reference

### `ListmonkClient`

**Construction**
- `ListmonkClient(base_url, username, password, timeout=30.0)` — explicit
- `ListmonkClient.from_env()` — classmethod, reads the three env vars above

**Read (GET endpoints, no side effects)**
- `get_campaign(campaign_id) -> dict` — raw campaign record
- `list_subscribers(list_ids, subscription_status=None) -> list[Subscriber]` — across one or more lists, deduped server-side; `list_ids` is required (no "everyone in the instance" mode)
- `campaign_recipients(campaign_id, subscription_status=None) -> list[Subscriber]` — convenience: reads the campaign's target lists and resolves their subscribers. Pass `subscription_status="confirmed"` (or other valid value) to filter; `None` means "everyone on the lists."
- `get_rendered_html(campaign_id) -> str` — Listmonk's server-rendered HTML (template applied) — what a recipient sees in their inbox
- `build_send_manifest(campaign_id) -> SendManifest` — structured pre-send snapshot (name, subject, status, target lists, recipients) for custom review/display. Call `manifest.format()` for a printable multi-line string.

**Write / action**
- `create_campaign(*, name, subject, body, list_ids=None, template_id=8, content_type="html") -> int` — POSTs a new draft, returns new campaign id. `template_id=8` is the OCHA Listmonk's canonical campaign template; override if you're pointing at a different Listmonk instance.
- `send_campaign(campaign_id, *, skip_confirmation=False, ask=input) -> None` — transitions campaign to `running` (the actual email-triggering call). Default prompts for confirmation; refuses status `"finished"` in both modes (re-sending would duplicate emails).
- `preview_in_browser(campaign_id) -> Path` — fetches rendered HTML, writes to a temp file, calls `webbrowser.open` on it

### Data types

- **`Subscriber`** — flattened subscriber record. Fields: `id`, `email`, `name`, `status` (*subscriber-level*: `enabled`/`disabled`/`blocklisted`), `raw` (full API payload). Method: `subscription_status_for(list_id) -> str | None` returns the *per-list* status (`confirmed`/`unconfirmed`/`unsubscribed`), which differs from `.status`.
- **`SendManifest`** — the pre-send manifest data object. Fields include `name`, `subject`, `status`, `target_lists: list[tuple[int, str]]`, `recipients: list[Subscriber]`. Method `.format() -> str` returns a printable multi-line snapshot (use it for inspect / log / Slack contexts; the dataclass `repr` is preserved for grep-friendly logging).

### Exceptions

- **`SendAborted`** — raised by `send_campaign` on name-mismatch, on status `"finished"`, or when the caller's `ask` callable returns anything that does not exactly match the campaign's `name`.

## Automation

For scheduled jobs where no human is available to type a confirmation:

```python
client.send_campaign(cid, skip_confirmation=True)
```

`skip_confirmation=True` bypasses the prompt but **does not bypass the `finished` refusal** — re-sending a completed campaign is always rejected.

For custom confirmation mechanisms (Slack bot, web form), pass an `ask` callable:

```python
def slack_ask(prompt: str) -> str:
    return slack.prompt_and_wait(prompt)

client.send_campaign(cid, ask=slack_ask)
```

## Scripts

Two manual smoke tests live in `scripts/`. They import the library and hit real Listmonk — intended for ad-hoc verification, not part of the automated test suite.

| Script | What it does | HTTP verbs |
|---|---|---|
| `scripts/smoke_check_recipients.py` | Inspect subscribers on a list or recipients of a campaign | GET only |
| `scripts/smoke_create_campaign.py` | Create a draft campaign (never sends) and verify it | POST + GET |

Both require `DSCI_LISTMONK_*` env vars set. Usage:

```bash
uv run python scripts/smoke_check_recipients.py subscribers <list_id>
uv run python scripts/smoke_check_recipients.py campaign <campaign_id>
uv run python scripts/smoke_create_campaign.py <list_id>
```

## Development

```bash
uv sync                  # install deps + dev group
uv run pytest            # run tests
uv run ruff check .      # lint
uv run ruff format .     # format
uv run mypy src          # type check
```

Tests are network-safe: `tests/conftest.py` patches `requests.Session.send` to refuse outbound HTTP, so a forgotten mock fails loudly rather than firing a real API call.

## References

- Listmonk API docs: https://listmonk.app/docs/apis/apis/ — consult before adding new
  endpoint wrappers; payload shapes and status semantics belong to upstream.
