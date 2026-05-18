"""Listmonk campaign client.

Listmonk API reference: https://listmonk.app/docs/apis/apis/
"""

from __future__ import annotations

import os
import tempfile
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

import requests

DEFAULT_CAMPAIGN_TEMPLATE_ID = 8
_SUBSCRIBERS_PAGE_SIZE = 100


class SendAborted(Exception):
    """Raised when ``send_campaign`` refuses to send or fails confirmation.

    Includes: typed-name mismatch, campaign status == ``"finished"``
    (refused even with ``skip_confirmation=True`` — re-sending would
    duplicate emails to recipients).
    """


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
class SendManifest:
    """The pre-send manifest for a campaign: who, what, where.

    Pure data. Produced by :meth:`ListmonkClient.build_send_manifest` and
    consumed by :meth:`ListmonkClient.send_campaign` to render the
    confirmation prompt, but useful on its own for notebook inspection,
    logging, or wiring into a custom confirmation UI (Slack, web form).

    Call :meth:`format` to get a printable multi-line snapshot string
    (campaign id, name, subject, status, target lists, recipients sample,
    plus a warning if status is not ``"draft"``). The dataclass ``repr``
    is preserved for grep-friendly logging.
    """

    campaign_id: int
    name: str
    subject: str
    status: str
    from_email: str | None
    target_lists: list[tuple[int, str]]
    recipients: list[Subscriber]
    raw_campaign: dict[str, Any]

    def format(self) -> str:
        """Return a human-readable multi-line snapshot of this manifest.

        Includes a status warning if the campaign is not in ``"draft"``
        state. Does NOT include any confirmation-prompt text — that lives
        in :func:`_format_send_confirmation_prompt` and is appended only
        when :meth:`ListmonkClient.send_campaign` is asking for input.
        """
        lines = [
            f"Send Manifest — Campaign {self.campaign_id}",
            f"  Name:    {self.name!r}",
            f"  Subject: {self.subject!r}",
            f"  Status:  {self.status!r}",
        ]
        if self.from_email:
            lines.append(f"  From:    {self.from_email!r}")
        lines.append("  Target Lists:")
        for lid, lname in self.target_lists:
            lines.append(f"    - [{lid}] {lname}")
        lines.append(f"  Recipients: {len(self.recipients)}")
        for r in self.recipients[:_CONFIRM_SAMPLE_LIMIT]:
            lines.append(
                f"    - {r.email}  subscriber_status={r.status!r}  name={r.name!r}"
            )
        remaining = len(self.recipients) - _CONFIRM_SAMPLE_LIMIT
        if remaining > 0:
            lines.append(f"    ... and {remaining} more (not shown)")

        if self.status != "draft":
            lines.append("")
            lines.append(
                f"  WARNING: status is {self.status!r}, not 'draft'. "
                f"Proceed only if intentional."
            )

        return "\n".join(lines)


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
        media_ids: list[int] | None = None,
    ) -> int:
        """Create a campaign in draft state. Returns the new campaign ID.

        The campaign is NOT sent — call ``send_campaign(id)`` to trigger
        delivery.

        ``template_id`` defaults to ``DEFAULT_CAMPAIGN_TEMPLATE_ID`` (``8``),
        which is the template id used on OCHA's Listmonk instance. If you
        are pointing this client at a different Listmonk, pass the
        ``template_id`` of that instance's campaign template — using ``8``
        blindly will either 400 or wrap your body in the wrong template.

        ``media_ids`` is a list of Listmonk media IDs to attach to the
        campaign email. Upload files first with :meth:`upload_attachment`
        to obtain their IDs.
        """
        payload: dict[str, Any] = {
            "name": name,
            "subject": subject,
            "lists": list_ids or [],
            "template_id": template_id,
            "type": "regular",
            "content_type": content_type,
            "body": body,
            "media": media_ids or [],
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

    def upload_media(self, data: bytes, filename: str = "image.png") -> str:
        """Upload a file to the Listmonk media library. Returns the hosted URL.

        ``data`` is the raw file bytes. ``filename`` sets the filename sent to
        Listmonk; the extension determines the MIME type (png, jpg, gif, pdf
        are all accepted by Listmonk). Use the returned URL in campaign HTML
        ``<img src="...">`` tags so images are hosted rather than inlined as
        data URIs — inline base64 inflates the email body and causes Gmail to
        clip messages over ~102 KB.

        Example::

            url = client.upload_media(png_bytes, "chart.png")
            html = f'<img src="{url}">'
        """
        import mimetypes

        mime_type, _ = mimetypes.guess_type(filename)
        if mime_type is None:
            mime_type = "application/octet-stream"
        r = requests.post(
            f"{self.base_url}/media",
            auth=self._auth,
            files={"file": (filename, data, mime_type)},
            timeout=self.timeout,
        )
        r.raise_for_status()
        url: str = r.json()["data"]["url"]
        return url

    def upload_attachment(self, data: bytes, filename: str) -> int:
        """Upload a file to Listmonk and return its media ID for use as an attachment.

        Unlike :meth:`upload_media` (which returns a URL for inline ``<img>``
        tags), this returns the integer media ID needed in the ``media_ids``
        list of :meth:`create_campaign` to attach the file to the email.

        Example::

            mid = client.upload_attachment(csv_bytes, "exposure.csv")
            cid = client.create_campaign(..., media_ids=[mid])
        """
        import mimetypes

        mime_type, _ = mimetypes.guess_type(filename)
        if mime_type is None:
            mime_type = "application/octet-stream"
        r = requests.post(
            f"{self.base_url}/media",
            auth=self._auth,
            files={"file": (filename, data, mime_type)},
            timeout=self.timeout,
        )
        r.raise_for_status()
        media_id: int = r.json()["data"]["id"]
        return media_id

    def send_campaign(
        self,
        campaign_id: int,
        *,
        skip_confirmation: bool = False,
        ask: Callable[[str], str] = input,
    ) -> None:
        """Transition a campaign to ``running`` (this is what actually sends).

        Default (``skip_confirmation=False``): build a :class:`SendManifest`,
        render it as a prompt string, call ``ask(prompt)`` to collect the
        caller's typed response, and only send if the response matches the
        campaign name exactly. Suitable for notebook / REPL use. In a
        headless environment (no stdin), the default ``ask=input`` will
        raise ``EOFError`` — which is the intended behaviour: no silent
        sends without human confirmation.

        ``skip_confirmation=True``: skip the prompt entirely. Use for
        automation (scheduled jobs, CI). The campaign still cannot be
        sent if its status is ``"finished"`` — that would duplicate
        emails to recipients and is hard-refused regardless of mode.

        ``ask`` is the confirmation callable. Defaults to ``input``. Pass
        a custom callable for tests, Slack-based confirmation, or other
        interactive UIs — it receives the full summary text as its
        single argument and must return the caller's response string.
        """
        if skip_confirmation:
            campaign = self.get_campaign(campaign_id)
            _refuse_if_finished(campaign_id, campaign.get("status", ""))
        else:
            manifest = self.build_send_manifest(campaign_id)
            _refuse_if_finished(campaign_id, manifest.status)
            answer = ask(_format_send_confirmation_prompt(manifest))
            if answer.strip() != manifest.name:
                raise SendAborted(
                    f"Confirmation mismatch: got {answer.strip()!r}, "
                    f"expected {manifest.name!r}. Send aborted."
                )

        r = requests.put(
            f"{self.base_url}/campaigns/{campaign_id}/status",
            auth=self._auth,
            json={"status": "running"},
            timeout=self.timeout,
        )
        r.raise_for_status()

    def build_send_manifest(self, campaign_id: int) -> SendManifest:
        """Assemble the pre-send manifest for a campaign.

        Fetches the campaign and its resolved recipients (deduped across
        target lists, no subscription-status filter applied). Returns a
        :class:`SendManifest` — pure data, no printing or prompting. Call
        ``manifest.format()`` for a printable snapshot.
        """
        campaign = self.get_campaign(campaign_id)
        target_lists: list[tuple[int, str]] = [
            (int(lst["id"]), lst.get("name", ""))
            for lst in campaign.get("lists", [])
            if "id" in lst
        ]
        list_ids = [lid for lid, _ in target_lists]
        recipients = self.list_subscribers(list_ids) if list_ids else []
        return SendManifest(
            campaign_id=campaign_id,
            name=campaign.get("name", ""),
            subject=campaign.get("subject", ""),
            status=campaign.get("status", ""),
            from_email=campaign.get("from_email"),
            target_lists=target_lists,
            recipients=recipients,
            raw_campaign=campaign,
        )

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

    def get_rendered_html(self, campaign_id: int) -> str:
        """Fetch the server-rendered HTML of a campaign.

        Hits ``GET /api/campaigns/{id}/preview`` and returns the HTML
        string Listmonk produces by wrapping the campaign's body in its
        template. This is what a recipient's email client would display.

        Different from ``get_campaign(id)["body"]``, which returns the
        raw body you stored — without the template applied.
        """
        r = requests.get(
            f"{self.base_url}/campaigns/{campaign_id}/preview",
            auth=self._auth,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.text

    def preview_in_browser(self, campaign_id: int) -> Path:
        """Fetch the rendered HTML and open it in the default browser.

        Writes the HTML to a named temp file, invokes
        :func:`webbrowser.open`, and returns the path. The temp file is
        NOT auto-deleted — the browser process may still be loading when
        this call returns — so the OS's temp-directory cleanup handles
        it eventually. Callers who want to remove it immediately can
        ``path.unlink()`` on the returned :class:`~pathlib.Path`.
        """
        html = self.get_rendered_html(campaign_id)
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".html",
            prefix=f"ocha_relay_preview_{campaign_id}_",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(html)
        path = Path(tmp.name)
        webbrowser.open(path.as_uri())
        return path

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
            subscribers.extend(Subscriber.from_api(row) for row in data["results"])
            # Defensive: a malformed per_page=0 response would wedge the
            # pagination check (0 * anything is never >= total). Bail
            # rather than loop forever.
            if data["per_page"] == 0:
                break
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
        list_ids = _extract_list_ids(campaign)
        if not list_ids:
            return []
        return self.list_subscribers(
            list_ids=list_ids,
            subscription_status=subscription_status,
        )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is required but not set.")
    return value


def _refuse_if_finished(campaign_id: int, status: str) -> None:
    if status == "finished":
        raise SendAborted(
            f"Campaign {campaign_id} has status 'finished'; "
            f"refusing to re-send (would duplicate emails)."
        )


def _extract_list_ids(campaign: dict[str, Any]) -> list[int]:
    return [int(lst["id"]) for lst in campaign.get("lists", []) if "id" in lst]


_CONFIRM_SAMPLE_LIMIT = 5


def _format_send_confirmation_prompt(manifest: SendManifest) -> str:
    return (
        manifest.format()
        + "\n\nAbout to trigger Listmonk to send this campaign."
        + "\nThis action cannot be undone."
        + "\n\nType the campaign name EXACTLY to confirm (anything else aborts): "
    )
