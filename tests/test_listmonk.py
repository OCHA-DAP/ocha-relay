import pytest

from ocha_relay.listmonk import ListmonkClient


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
