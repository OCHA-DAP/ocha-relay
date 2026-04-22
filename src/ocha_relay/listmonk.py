"""Listmonk campaign client.

Listmonk API reference: https://listmonk.app/docs/apis/apis/
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Self

import requests

DEFAULT_CAMPAIGN_TEMPLATE_ID = 8


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


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Environment variable {name} is required but not set."
        )
    return value
