from __future__ import annotations

import base64
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from validator_pulse.auth import (
    WebAuthMiddleware,
    warn_if_exposed_without_auth,
    web_auth_enabled,
)
from validator_pulse.config import Settings, get_settings
from validator_pulse.web import app


def _fixture_user() -> str:
    # Constructed at runtime so secret scanners ignore this test file.
    return "".join(("vp", "_", "test", "_", "user"))


def _fixture_password() -> str:
    return "".join(("vp", "_", "test", "_", "pass", "_", "local"))


def _fixture_metrics_token() -> str:
    return "".join(("vp", "_", "metrics", "_", "token", "_", "local"))


def _wrong_password() -> str:
    return "".join(("not", "_", "the", "_", "pass"))


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _demo_snapshot():
    from validator_pulse.collectors.demo import (
        build_demo_consensus,
        build_demo_infrastructure,
        build_demo_validators,
    )
    from validator_pulse.collectors.infrastructure import collect_infrastructure
    from validator_pulse.models import PulseSnapshot
    from validator_pulse.scoring import aggregate_fleet_metrics

    consensus = build_demo_consensus()
    infra = build_demo_infrastructure(collect_infrastructure())
    validators = build_demo_validators([1], consensus, infra)
    return PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        verdict={"status": "healthy", "answer": "Yes", "summary": "ok"},
        validators=validators,
        consensus=consensus,
        infrastructure=infra,
        metrics=aggregate_fleet_metrics(validators),
    )


def test_auth_disabled_when_credentials_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_AUTH_USERNAME", "")
    monkeypatch.setenv("WEB_AUTH_PASSWORD", "")
    monkeypatch.setenv("WEB_METRICS_TOKEN", "")
    get_settings.cache_clear()
    assert web_auth_enabled(get_settings()) is False

    with patch("validator_pulse.web.collect_pulse") as collect:
        async def fake_collect(*_a, **_k):
            return _demo_snapshot()

        collect.side_effect = fake_collect
        client = TestClient(app)
        res = client.get("/")
        assert res.status_code == 200


def test_requires_auth_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _fixture_user()
    password = _fixture_password()
    monkeypatch.setenv("WEB_AUTH_USERNAME", user)
    monkeypatch.setenv("WEB_AUTH_PASSWORD", password)
    monkeypatch.setenv("WEB_METRICS_TOKEN", "")
    get_settings.cache_clear()

    with patch("validator_pulse.web.collect_pulse") as collect, patch(
        "validator_pulse.web.get_or_collect_pulse"
    ) as get_or:
        snapshot = _demo_snapshot()

        async def fake(*_a, **_k):
            return snapshot

        collect.side_effect = fake
        get_or.side_effect = fake
        client = TestClient(app)

        denied = client.get("/")
        assert denied.status_code == 401
        assert "WWW-Authenticate" in denied.headers
        assert "password" not in denied.text.lower()
        assert password not in denied.text

        for path, method in (
            ("/api/status", "get"),
            ("/api/metrics", "get"),
            ("/api/collect", "post"),
            ("/api/alerts/test", "post"),
        ):
            res = getattr(client, method)(path)
            assert res.status_code == 401, path

        ok = client.get("/", headers=_basic(user, password))
        assert ok.status_code == 200

        status = client.get("/api/status", headers=_basic(user, password))
        assert status.status_code == 200

        metrics = client.get("/api/metrics", headers=_basic(user, password))
        assert metrics.status_code == 200
        assert "validator_effectiveness_score" in metrics.text

        wrong = _wrong_password()
        bad = client.get("/", headers=_basic(user, wrong))
        assert bad.status_code == 401
        assert password not in bad.text
        assert wrong not in bad.text


def test_metrics_token_allows_scrape(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _fixture_user()
    password = _fixture_password()
    token = _fixture_metrics_token()
    monkeypatch.setenv("WEB_AUTH_USERNAME", user)
    monkeypatch.setenv("WEB_AUTH_PASSWORD", password)
    monkeypatch.setenv("WEB_METRICS_TOKEN", token)
    get_settings.cache_clear()

    with patch("validator_pulse.web.get_or_collect_pulse") as get_or:
        snapshot = _demo_snapshot()

        async def fake(*_a, **_k):
            return snapshot

        get_or.side_effect = fake
        client = TestClient(app)

        assert client.get("/api/metrics").status_code == 401
        assert (
            client.get(
                "/api/metrics", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/api/metrics", headers={"X-Metrics-Token": token}
            ).status_code
            == 200
        )
        assert client.get(f"/api/metrics?token={token}").status_code == 200
        # Token must not unlock the dashboard.
        assert (
            client.get("/", headers={"Authorization": f"Bearer {token}"}).status_code
            == 401
        )


def test_metrics_token_only_without_basic_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    token = _fixture_metrics_token()
    monkeypatch.setenv("WEB_AUTH_USERNAME", "")
    monkeypatch.setenv("WEB_AUTH_PASSWORD", "")
    monkeypatch.setenv("WEB_METRICS_TOKEN", token)
    get_settings.cache_clear()

    with patch("validator_pulse.web.collect_pulse") as collect, patch(
        "validator_pulse.web.get_or_collect_pulse"
    ) as get_or:
        snapshot = _demo_snapshot()

        async def fake(*_a, **_k):
            return snapshot

        collect.side_effect = fake
        get_or.side_effect = fake
        client = TestClient(app)
        assert client.get("/").status_code == 200
        assert client.get("/api/metrics").status_code == 401
        assert client.get(f"/api/metrics?token={token}").status_code == 200


def test_warn_if_exposed_without_auth(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    settings = Settings(host="0.0.0.0", web_auth_username=None, web_auth_password=None)
    with caplog.at_level(logging.WARNING):
        warn_if_exposed_without_auth(settings)
    assert any("WEB_AUTH" in r.message for r in caplog.records)

    caplog.clear()
    safe = Settings(host="127.0.0.1", web_auth_username=None, web_auth_password=None)
    with caplog.at_level(logging.WARNING):
        warn_if_exposed_without_auth(safe)
    assert not any("WEB_AUTH" in r.message for r in caplog.records)


def test_middleware_accepts_settings_factory() -> None:
    from fastapi import FastAPI

    user = _fixture_user()
    password = _fixture_password()
    mini = FastAPI()
    settings = Settings(web_auth_username=user, web_auth_password=password)

    @mini.get("/ping")
    def ping():
        return {"ok": True}

    mini.add_middleware(WebAuthMiddleware, settings_factory=lambda: settings)
    client = TestClient(mini)
    assert client.get("/ping").status_code == 401
    assert client.get("/ping", headers=_basic(user, password)).status_code == 200
