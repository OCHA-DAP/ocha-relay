from __future__ import annotations

from typing import Any

import pytest
import requests

from ocha_relay.listmonk import (
    ListmonkClient,
    SendAborted,
    SendSummary,
    Subscriber,
)


def _client() -> ListmonkClient:
    return ListmonkClient(
        base_url="https://listmonk.example.org/api",
        username="u",
        password="p",
    )


class _FakeResponse:
    """Minimal stand-in for ``requests.Response`` — enough for our code."""

    def __init__(
        self,
        json_body: dict[str, Any] | None = None,
        status_code: int = 200,
        text: str = "",
    ) -> None:
        self._json = json_body
        self.status_code = status_code
        self.text = text

    def json(self) -> dict[str, Any]:
        if self._json is None:
            raise ValueError("no json body set on fake response")
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


def test_from_env_reads_all_three_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DSCI_LISTMONK_BASE_URL", "https://listmonk.example.org/api/")
    monkeypatch.setenv("DSCI_LISTMONK_API_USERNAME", "user")
    monkeypatch.setenv("DSCI_LISTMONK_API_KEY", "secret")

    client = ListmonkClient.from_env()

    assert client.base_url == "https://listmonk.example.org/api"  # trailing / stripped
    assert client.username == "user"
    assert client.password == "secret"


def test_from_env_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DSCI_LISTMONK_BASE_URL", raising=False)
    monkeypatch.delenv("DSCI_LISTMONK_API_USERNAME", raising=False)
    monkeypatch.delenv("DSCI_LISTMONK_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="DSCI_LISTMONK_BASE_URL"):
        ListmonkClient.from_env()


# ---------- list_subscribers ----------


def _page(
    results: list[dict[str, Any]], total: int, page: int, per_page: int
) -> dict[str, Any]:
    return {
        "data": {"results": results, "total": total, "page": page, "per_page": per_page}
    }


def _sub(id_: int, email: str) -> dict[str, Any]:
    return {"id": id_, "email": email, "name": email.split("@")[0], "status": "enabled"}


def test_list_subscribers_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="list_ids"):
        _client().list_subscribers([])


def test_list_subscribers_single_page(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["params"] = kwargs["params"]
        rows = [_sub(1, "a@x.org"), _sub(2, "b@x.org")]
        return _FakeResponse(_page(rows, total=2, page=1, per_page=100))

    monkeypatch.setattr(requests, "get", fake_get)

    result = _client().list_subscribers([5])

    assert [s.email for s in result] == ["a@x.org", "b@x.org"]
    assert all(isinstance(s, Subscriber) for s in result)
    assert captured["url"] == "https://listmonk.example.org/api/subscribers"
    # list_id param repeated; page + per_page present
    assert ("list_id", 5) in captured["params"]
    assert ("page", 1) in captured["params"]
    assert ("per_page", 100) in captured["params"]


def test_list_subscribers_repeats_list_id_for_multiple_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        captured["params"] = kwargs["params"]
        return _FakeResponse(_page([], total=0, page=1, per_page=100))

    monkeypatch.setattr(requests, "get", fake_get)

    _client().list_subscribers([1, 2, 3])

    list_id_pairs = [p for p in captured["params"] if p[0] == "list_id"]
    assert list_id_pairs == [("list_id", 1), ("list_id", 2), ("list_id", 3)]


def test_list_subscribers_paginates_until_total_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        page = dict(kwargs["params"])["page"]
        calls.append(page)
        if page == 1:
            return _FakeResponse(
                _page(
                    [_sub(1, "a@x.org"), _sub(2, "b@x.org")],
                    total=3,
                    page=1,
                    per_page=2,
                )
            )
        return _FakeResponse(_page([_sub(3, "c@x.org")], total=3, page=2, per_page=2))

    monkeypatch.setattr(requests, "get", fake_get)

    # Shrink page size just for this test by patching the constant.
    from ocha_relay import listmonk as mod

    monkeypatch.setattr(mod, "_SUBSCRIBERS_PAGE_SIZE", 2)

    result = _client().list_subscribers([5])

    assert [s.id for s in result] == [1, 2, 3]
    assert calls == [1, 2]


def test_list_subscribers_passes_subscription_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        captured["params"] = kwargs["params"]
        return _FakeResponse(_page([], total=0, page=1, per_page=100))

    monkeypatch.setattr(requests, "get", fake_get)

    _client().list_subscribers([5], subscription_status="confirmed")

    assert ("subscription_status", "confirmed") in captured["params"]


# ---------- campaign_recipients ----------


def test_campaign_recipients_resolves_lists_and_fetches_union(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_params: list[Any] = []

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        if url.endswith("/campaigns/42"):
            return _FakeResponse(
                {
                    "data": {
                        "id": 42,
                        "lists": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
                    }
                }
            )
        if url.endswith("/subscribers"):
            captured_params.append(kwargs["params"])
            return _FakeResponse(
                _page([_sub(9, "alice@x.org")], total=1, page=1, per_page=100)
            )
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(requests, "get", fake_get)

    result = _client().campaign_recipients(42)

    assert [s.email for s in result] == ["alice@x.org"]
    params = captured_params[0]
    # Both list ids were forwarded.
    assert [p for p in params if p[0] == "list_id"] == [("list_id", 1), ("list_id", 2)]
    # Default: no subscription_status filter, so the key is absent entirely.
    assert not any(k == "subscription_status" for k, _ in params)


def test_subscription_status_for_reads_per_list_status() -> None:
    sub = Subscriber.from_api(
        {
            "id": 1,
            "email": "a@x.org",
            "name": "A",
            "status": "enabled",
            "lists": [
                {"id": 5, "subscription_status": "confirmed"},
                {"id": 9, "subscription_status": "unconfirmed"},
            ],
        }
    )
    assert sub.subscription_status_for(5) == "confirmed"
    assert sub.subscription_status_for(9) == "unconfirmed"
    assert sub.subscription_status_for(999) is None  # not on that list


def test_campaign_recipients_returns_empty_when_campaign_has_no_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        assert url.endswith("/campaigns/99"), (
            "should not call /subscribers when campaign has no lists"
        )
        return _FakeResponse({"data": {"id": 99, "lists": []}})

    monkeypatch.setattr(requests, "get", fake_get)

    assert _client().campaign_recipients(99) == []


# ---------- create_campaign ----------


def test_create_campaign_posts_to_campaigns_and_returns_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = kwargs["json"]
        captured["auth"] = kwargs["auth"]
        return _FakeResponse({"data": {"id": 99}})

    monkeypatch.setattr(requests, "post", fake_post)

    result = _client().create_campaign(
        name="w2-update",
        subject="Weekly update",
        body="<p>hello</p>",
        list_ids=[3, 4],
    )

    assert result == 99
    assert captured["url"] == "https://listmonk.example.org/api/campaigns"
    assert captured["auth"] == ("u", "p")
    payload = captured["json"]
    assert payload["name"] == "w2-update"
    assert payload["subject"] == "Weekly update"
    assert payload["body"] == "<p>hello</p>"
    assert payload["lists"] == [3, 4]
    # These two are design invariants we do NOT want accidentally changed:
    # "regular" is what distinguishes this from "optin"/"transactional".
    assert payload["type"] == "regular"
    assert payload["content_type"] == "html"


def test_create_campaign_defaults_empty_lists_when_none_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured["json"] = kwargs["json"]
        return _FakeResponse({"data": {"id": 1}})

    monkeypatch.setattr(requests, "post", fake_post)

    _client().create_campaign(name="n", subject="s", body="b")

    assert captured["json"]["lists"] == []


def test_create_campaign_uses_default_template_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ocha_relay.listmonk import DEFAULT_CAMPAIGN_TEMPLATE_ID

    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured["json"] = kwargs["json"]
        return _FakeResponse({"data": {"id": 1}})

    monkeypatch.setattr(requests, "post", fake_post)

    _client().create_campaign(name="n", subject="s", body="b")

    assert captured["json"]["template_id"] == DEFAULT_CAMPAIGN_TEMPLATE_ID


# ---------- send_campaign ----------


def test_send_campaign_skip_confirmation_puts_running_to_status_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """skip_confirmation=True path: one status-check GET, then the PUT."""
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        assert url.endswith("/campaigns/42")
        return _FakeResponse({"data": {"id": 42, "status": "draft"}})

    def fake_put(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = kwargs["json"]
        captured["auth"] = kwargs["auth"]
        return _FakeResponse({"data": {}})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "put", fake_put)

    result = _client().send_campaign(42, skip_confirmation=True)

    assert result is None
    assert captured["url"] == (
        "https://listmonk.example.org/api/campaigns/42/status"
    )
    # Exact payload is the critical assertion — this is the single API
    # call that tells Listmonk to start sending.
    assert captured["json"] == {"status": "running"}
    assert captured["auth"] == ("u", "p")


def test_send_campaign_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse({"data": {"id": 42, "status": "draft"}})

    def fake_put(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse({"data": {}}, status_code=500)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "put", fake_put)

    with pytest.raises(requests.HTTPError):
        _client().send_campaign(42, skip_confirmation=True)


# ---------- build_send_summary + confirmation flow ----------


def _fake_campaign(
    campaign_id: int = 42,
    name: str = "Weekly Update",
    status: str = "draft",
    lists: list[dict[str, Any]] | None = None,
    subject: str = "Subj",
    from_email: str = "from@x.org",
) -> dict[str, Any]:
    return {
        "data": {
            "id": campaign_id,
            "name": name,
            "subject": subject,
            "status": status,
            "from_email": from_email,
            "lists": lists if lists is not None else [{"id": 1, "name": "L1"}],
        }
    }


def test_build_send_summary_assembles_campaign_and_recipients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        if url.endswith("/campaigns/42"):
            return _FakeResponse(
                _fake_campaign(
                    lists=[{"id": 1, "name": "L1"}, {"id": 2, "name": "L2"}]
                )
            )
        if url.endswith("/subscribers"):
            return _FakeResponse(_page([_sub(9, "a@x.org")], 1, 1, 100))
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(requests, "get", fake_get)

    summary = _client().build_send_summary(42)

    assert isinstance(summary, SendSummary)
    assert summary.campaign_id == 42
    assert summary.name == "Weekly Update"
    assert summary.subject == "Subj"
    assert summary.status == "draft"
    assert summary.from_email == "from@x.org"
    assert summary.target_lists == [(1, "L1"), (2, "L2")]
    assert [r.email for r in summary.recipients] == ["a@x.org"]


def test_send_campaign_prompts_and_sends_when_name_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    put_fired: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        if url.endswith("/campaigns/42"):
            return _FakeResponse(_fake_campaign())
        if url.endswith("/subscribers"):
            return _FakeResponse(_page([], 0, 1, 100))
        raise AssertionError(f"unexpected url: {url}")

    def fake_put(url: str, **kwargs: Any) -> _FakeResponse:
        put_fired["url"] = url
        put_fired["json"] = kwargs["json"]
        return _FakeResponse({"data": {}})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "put", fake_put)

    received_prompt: dict[str, Any] = {}

    def ask(prompt: str) -> str:
        received_prompt["text"] = prompt
        return "Weekly Update"  # matches fake_campaign default

    _client().send_campaign(42, ask=ask)

    assert put_fired["json"] == {"status": "running"}
    # Summary text should have been passed to ask, not just a short prompt.
    assert "Weekly Update" in received_prompt["text"]
    assert "Send Summary" in received_prompt["text"]


def test_send_campaign_raises_when_typed_name_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        if url.endswith("/campaigns/42"):
            return _FakeResponse(_fake_campaign())
        if url.endswith("/subscribers"):
            return _FakeResponse(_page([], 0, 1, 100))
        raise AssertionError(f"unexpected url: {url}")

    def fake_put(url: str, **kwargs: Any) -> _FakeResponse:
        raise AssertionError("PUT must not fire when confirmation fails")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "put", fake_put)

    with pytest.raises(SendAborted, match="Confirmation mismatch"):
        _client().send_campaign(42, ask=lambda _p: "wrong name")


def test_send_campaign_refuses_finished_status_in_interactive_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        if url.endswith("/campaigns/42"):
            return _FakeResponse(_fake_campaign(status="finished"))
        if url.endswith("/subscribers"):
            return _FakeResponse(_page([], 0, 1, 100))
        raise AssertionError(f"unexpected url: {url}")

    def fake_put(url: str, **kwargs: Any) -> _FakeResponse:
        raise AssertionError("PUT must not fire against a finished campaign")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "put", fake_put)

    # ask is never called because status check happens first; use a
    # sentinel that would fail loudly if it ever were.
    def ask(_p: str) -> str:
        raise AssertionError("ask must not be called for finished campaigns")

    with pytest.raises(SendAborted, match="finished"):
        _client().send_campaign(42, ask=ask)


def test_send_campaign_refuses_finished_status_even_with_skip_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse({"data": {"id": 42, "status": "finished"}})

    def fake_put(url: str, **kwargs: Any) -> _FakeResponse:
        raise AssertionError("PUT must not fire against a finished campaign")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "put", fake_put)

    with pytest.raises(SendAborted, match="finished"):
        _client().send_campaign(42, skip_confirmation=True)


# ---------- get_rendered_html + preview_in_browser ----------


def test_get_rendered_html_returns_response_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["auth"] = kwargs["auth"]
        return _FakeResponse(text="<html><body>rendered</body></html>")

    monkeypatch.setattr(requests, "get", fake_get)

    html = _client().get_rendered_html(42)

    assert html == "<html><body>rendered</body></html>"
    assert captured["url"] == (
        "https://listmonk.example.org/api/campaigns/42/preview"
    )
    assert captured["auth"] == ("u", "p")


def test_get_rendered_html_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(status_code=500, text="server error")

    monkeypatch.setattr(requests, "get", fake_get)

    with pytest.raises(requests.HTTPError):
        _client().get_rendered_html(42)


def test_preview_in_browser_writes_html_and_opens_default_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetches rendered HTML, writes it to a temp file, invokes webbrowser.open
    with that file's URI, and returns the Path."""
    import webbrowser

    from ocha_relay import listmonk as mod

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(text="<html>preview body</html>")

    opened_with: dict[str, str] = {}

    def fake_open(url: str, *_args: Any, **_kwargs: Any) -> bool:
        opened_with["url"] = url
        return True

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(mod.webbrowser, "open", fake_open)
    # Belt & braces: also patch the imported-here alias in case of module
    # identity drift (same underlying module, separate test visibility).
    monkeypatch.setattr(webbrowser, "open", fake_open)

    path = _client().preview_in_browser(42)

    assert path.exists(), "temp file should persist after call"
    assert path.suffix == ".html"
    assert path.read_text(encoding="utf-8") == "<html>preview body</html>"
    assert opened_with["url"] == path.as_uri()
    assert opened_with["url"].startswith("file://")

    # Test-side cleanup — library deliberately leaves the temp file so
    # the browser has time to load it.
    path.unlink()
