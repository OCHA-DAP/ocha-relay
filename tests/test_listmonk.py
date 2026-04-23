from __future__ import annotations

from typing import Any

import pytest
import requests

from ocha_relay.listmonk import ListmonkClient, Subscriber


def _client() -> ListmonkClient:
    return ListmonkClient(
        base_url="https://listmonk.example.org/api",
        username="u",
        password="p",
    )


class _FakeResponse:
    """Minimal stand-in for ``requests.Response`` — enough for our code."""

    def __init__(self, json_body: dict[str, Any], status_code: int = 200) -> None:
        self._json = json_body
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
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
