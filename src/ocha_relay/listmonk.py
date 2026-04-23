"""Listmonk campaign client.

Listmonk API reference: https://listmonk.app/docs/apis/apis/
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Self

import requests

DEFAULT_CAMPAIGN_TEMPLATE_ID = 8
_SUBSCRIBERS_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class Subscriber:
    """A Listmonk subscriber, flattened to the fields used most often.

    ``status`` is the *subscriber-level* state (enabled / disabled /
    blocklisted). The *per-list* subscription state (confirmed /
    unconfirmed / unsubscribed) lives inside ``raw["lists"]`` — one
    entry per list the subscriber is on — and is retrievable via
    :meth:`subscription_status_for`.
    """

    id: int
    email: str
    name: str
    status: str
    raw: dict[str, Any]

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> Self:
        return cls(
            id=payload["id"],
            email=payload["email"],
            name=payload["name"],
            status=payload["status"],
            raw=payload,
        )

    def subscription_status_for(self, list_id: int) -> str | None:
        """Return this subscriber's per-list subscription status.

        Returns ``None`` if the subscriber is not on the given list in
        the response we received (which means either they are truly not
        on it, or we didn't request it).
        """
        for lst in self.raw.get("lists", []):
            if lst.get("id") == list_id:
                status = lst.get("subscription_status")
                return status if isinstance(status, str) else None
        return None


@dataclass(frozen=True, slots=True)
class ListmonkClient:
    """Thin client for Listmonk's campaigns API.

    Holds base URL + basic-auth credentials so callers can construct
    multiple clients (e.g. staging vs prod) and pass them around.
    """

    base_url: str
    username: str
    password: str
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> Self:
        """Build a client from env vars.

        Required: DSCI_LISTMONK_BASE_URL, DSCI_LISTMONK_API_USERNAME,
        DSCI_LISTMONK_API_KEY.
        """
        base_url = _require_env("DSCI_LISTMONK_BASE_URL")
        username = _require_env("DSCI_LISTMONK_API_USERNAME")
        password = _require_env("DSCI_LISTMONK_API_KEY")
        return cls(
            base_url=base_url.rstrip("/"),
            username=username,
            password=password,
        )

    @property
    def _auth(self) -> tuple[str, str]:
        return (self.username, self.password)

    def create_campaign(
        self,
        *,
        name: str,
        subject: str,
        body: str,
        list_ids: list[int] | None = None,
        template_id: int = DEFAULT_CAMPAIGN_TEMPLATE_ID,
        content_type: str = "html",
    ) -> int:
        """Create a campaign in draft state. Returns the new campaign ID.

        The campaign is NOT sent — call ``send_campaign(id)`` to trigger
        delivery.
        """
        payload: dict[str, Any] = {
            "name": name,
            "subject": subject,
            "lists": list_ids or [],
            "template_id": template_id,
            "type": "regular",
            "content_type": content_type,
            "body": body,
        }
        r = requests.post(
            f"{self.base_url}/campaigns",
            auth=self._auth,
            json=payload,
            timeout=self.timeout,
        )
        r.raise_for_status()
        campaign_id: int = r.json()["data"]["id"]
        return campaign_id

    def send_campaign(self, campaign_id: int) -> None:
        """Transition a draft campaign to ``running`` so Listmonk sends it."""
        r = requests.put(
            f"{self.base_url}/campaigns/{campaign_id}/status",
            auth=self._auth,
            json={"status": "running"},
            timeout=self.timeout,
        )
        r.raise_for_status()

    def get_campaign(self, campaign_id: int) -> dict[str, Any]:
        """Fetch a campaign record. Returns the ``data`` dict from the API."""
        r = requests.get(
            f"{self.base_url}/campaigns/{campaign_id}",
            auth=self._auth,
            timeout=self.timeout,
        )
        r.raise_for_status()
        data: dict[str, Any] = r.json()["data"]
        return data

    def list_subscribers(
        self,
        list_ids: list[int],
        subscription_status: str | None = None,
    ) -> list[Subscriber]:
        """Fetch subscribers across one or more lists, deduped by Listmonk.

        ``list_ids`` is required — we intentionally do not expose a
        "fetch every subscriber in the instance" mode, which would cross
        project boundaries in a shared Listmonk deployment.

        If a subscriber belongs to several of the requested lists, they
        appear once (Listmonk dedupes server-side via SQL join).
        """
        if not list_ids:
            raise ValueError("list_ids must contain at least one list id")

        base_params: list[tuple[str, str | int]] = [
            ("list_id", lid) for lid in list_ids
        ]
        if subscription_status:
            base_params.append(("subscription_status", subscription_status))
        base_params.append(("per_page", _SUBSCRIBERS_PAGE_SIZE))

        subscribers: list[Subscriber] = []
        page = 1
        while True:
            r = requests.get(
                f"{self.base_url}/subscribers",
                auth=self._auth,
                params=[*base_params, ("page", page)],
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()["data"]
            subscribers.extend(
                Subscriber.from_api(row) for row in data["results"]
            )
            if page * data["per_page"] >= data["total"]:
                break
            page += 1
        return subscribers

    def campaign_recipients(
        self,
        campaign_id: int,
        subscription_status: str | None = None,
    ) -> list[Subscriber]:
        """Preview who is on this campaign's target lists.

        Fetches the campaign, reads its target lists, and returns the
        deduped union of subscribers across those lists. By default no
        subscription-status filter is applied — you get everyone on the
        lists, regardless of whether they are ``confirmed``,
        ``unconfirmed``, or ``unsubscribed``.

        This is an *approximation* of Listmonk's send-time recipient
        resolution: Listmonk itself applies a filter whose behavior
        depends on each list's opt-in configuration (single vs double)
        and on blocklist state. For a tighter estimate, pass a specific
        ``subscription_status`` (e.g. ``"confirmed"``) matching your
        list's setup. Do not treat the result as authoritative — new
        signups, list changes, or blocklist updates between this call
        and the actual send will cause drift.
        """
        campaign = self.get_campaign(campaign_id)
        list_ids = [lst["id"] for lst in campaign.get("lists", [])]
        if not list_ids:
            return []
        return self.list_subscribers(
            list_ids=list_ids,
            subscription_status=subscription_status,
        )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Environment variable {name} is required but not set."
        )
    return value
