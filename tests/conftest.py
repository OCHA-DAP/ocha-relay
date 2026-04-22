"""Test-wide guard: no outbound HTTP during the test suite.

Autouse fixture patches ``requests.Session.send`` — the single method every
request in the ``requests`` library flows through (including the top-level
``requests.post``, ``requests.put``, etc. which use a module-level session).
Any test that tries to hit the real network will fail loudly instead of
sending a real email.

To write a test that simulates an API response, inject a fake via your own
fixture (e.g. a mocked Session) rather than unpinning this guard.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests


@pytest.fixture(autouse=True)
def block_outbound_http(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(
            "Outbound HTTP is disabled in tests. If you need to simulate a "
            "Listmonk response, inject a mocked requests.Session via a "
            "fixture instead of calling the real API."
        )

    monkeypatch.setattr(requests.Session, "send", _blocked)
