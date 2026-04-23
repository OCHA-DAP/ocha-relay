"""Manual smoke test against real Listmonk. NOT part of the test suite.

Read-only — only calls GET endpoints. Never sends an email.

Prints minimally (count + a few samples) to avoid dumping hundreds of
real email addresses into your terminal scrollback.

Usage (requires DSCI_LISTMONK_* env vars set)::

    uv run python scripts/smoke_listmonk.py subscribers <list_id>
    uv run python scripts/smoke_listmonk.py campaign <campaign_id>
"""

from __future__ import annotations

import sys

from ocha_relay.listmonk import ListmonkClient

SAMPLE_LIMIT = 5


def cmd_subscribers(client: ListmonkClient, list_id: int) -> None:
    print(f"Fetching subscribers for list {list_id}...")
    subs = client.list_subscribers([list_id])
    print(f"  total: {len(subs)}")
    for s in subs[:SAMPLE_LIMIT]:
        print(
            f"    {s.email}  subscriber_status={s.status!r}  "
            f"list_status={s.subscription_status_for(list_id)!r}  "
            f"name={s.name!r}"
        )
    if len(subs) > SAMPLE_LIMIT:
        print(f"    ... and {len(subs) - SAMPLE_LIMIT} more (not printed)")


def cmd_campaign(client: ListmonkClient, campaign_id: int) -> None:
    print(f"Fetching campaign {campaign_id}...")
    campaign = client.get_campaign(campaign_id)
    print(f"  name:   {campaign.get('name')!r}")
    print(f"  status: {campaign.get('status')!r}")
    lists = campaign.get("lists", [])
    list_ids = [int(lst["id"]) for lst in lists if "id" in lst]
    print(f"  target lists: {[(lst.get('id'), lst.get('name')) for lst in lists]}")

    print("Resolving recipients (read-only — no send, no filter)...")
    recips = client.campaign_recipients(campaign_id)
    print(f"  total: {len(recips)}")
    for s in recips[:SAMPLE_LIMIT]:
        per_list = {lid: s.subscription_status_for(lid) for lid in list_ids}
        per_list = {k: v for k, v in per_list.items() if v is not None}
        print(
            f"    {s.email}  subscriber_status={s.status!r}  "
            f"per_list_status={per_list}  name={s.name!r}"
        )
    if len(recips) > SAMPLE_LIMIT:
        print(f"    ... and {len(recips) - SAMPLE_LIMIT} more (not printed)")


def main() -> None:
    args = sys.argv[1:]
    if len(args) != 2 or args[0] not in {"subscribers", "campaign"}:
        sys.exit(
            "usage:\n"
            "  smoke_listmonk.py subscribers <list_id>\n"
            "  smoke_listmonk.py campaign <campaign_id>"
        )

    subcommand, raw_id = args
    try:
        numeric_id = int(raw_id)
    except ValueError:
        sys.exit(f"id must be an integer, got {raw_id!r}")

    client = ListmonkClient.from_env()
    if subcommand == "subscribers":
        cmd_subscribers(client, numeric_id)
    else:
        cmd_campaign(client, numeric_id)


if __name__ == "__main__":
    main()
