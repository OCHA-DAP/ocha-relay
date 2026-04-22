# ocha-relay

Internal utilities for the OCHA data science team's automated tools and communications.

## Status

Early scaffold. Currently provides a Listmonk campaign client. SMTP + Jinja helpers coming later.

## Install (uv)

```bash
uv sync
```

For editable use from another uv-managed project:

```bash
uv add --editable path/to/ocha-relay
```

## Quick start — Listmonk campaigns

```python
from ocha_relay.listmonk import ListmonkClient

# Reads DSCI_LISTMONK_API_USERNAME, DSCI_LISTMONK_API_KEY,
# and DSCI_LISTMONK_BASE_URL from the environment.
client = ListmonkClient.from_env()

campaign_id = client.create_campaign(
    name="weekly_update",
    subject="Weekly update",
    list_ids=[1, 2],
    body="<h1>Hello</h1>",
)
client.send_campaign(campaign_id)
```

## Development

```bash
uv sync                  # install deps + dev group
uv run pytest            # run tests
uv run ruff check .      # lint
uv run ruff format .     # format
uv run mypy src          # type check
```

## References

- Listmonk API docs: https://listmonk.app/docs/apis/apis/ — consult before adding new
  endpoint wrappers; payload shapes and status semantics belong to upstream.
