"""Manual smoke test: create a DRAFT campaign via ocha-relay.

Writes to Listmonk state (POST /api/campaigns) — creates a draft
campaign visible in the Listmonk admin. Does NOT call send_campaign,
so no email is triggered. Clean up the draft from the admin UI
afterward.

If you are looking for a read-only smoke test (subscribers, campaign
inspection), use scripts/smoke_listmonk.py instead.

Usage (requires DSCI_LISTMONK_* env vars set)::

    uv run python scripts/smoke_create_campaign.py <list_id>
"""

from __future__ import annotations

import sys

from ocha_relay.listmonk import ListmonkClient


def main() -> None:
    args = sys.argv[1:]
    if len(args) != 1:
        sys.exit("usage: smoke_create_campaign.py <list_id>")
    try:
        list_id = int(args[0])
    except ValueError:
        sys.exit(f"list_id must be an integer, got {args[0]!r}")

    client = ListmonkClient.from_env()

    print(f"Creating a DRAFT campaign targeting list {list_id}...")
    print("  (create_campaign only makes a draft; send_campaign is never called)")
    cid = client.create_campaign(
        name="ocha-relay smoke test — delete me",
        subject="[TEST] ocha-relay create_campaign smoke",
        body="<p>Smoke test draft from ocha-relay. Safe to delete.</p>",
        list_ids=[list_id],
    )
    print(f"  Created draft campaign id={cid}")

    print("\nFetching the new draft to verify its state...")
    campaign = client.get_campaign(cid)
    print(f"  name:   {campaign.get('name')!r}")
    print(f"  status: {campaign.get('status')!r}  (should be 'draft')")
    lists = campaign.get("lists", [])
    print(f"  target lists: {[(lst.get('id'), lst.get('name')) for lst in lists]}")

    print(f"\nClean up: delete draft id={cid} in the Listmonk admin UI.")


if __name__ == "__main__":
    main()
