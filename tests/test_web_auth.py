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


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_auth_disabled_when_credentials_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_AUTH_USERNAME", "")
    monkeypatch.setenv("WEB_AUTH_PASSWORD", "")
    monkeypatch.setenv("WEB_METRICS_TOKEN", "")
    get_settings.cache_clear()
    assert web_auth_enabled(get_settings()) is False

    with patch("validator_pulse.web.collect_pulse") as collect:
        async def fake_collect(*_a, **_k):
            from validator_pulse.models import PulseSnapshot
            from validator_pulse.scoring import aggregate_fleet_metrics
            from validator_pulse.collectors.demo import (
                build_demo_consensus,
                build_demo_infrastructure,
                build_demo_validators,
            )
            from validator_pulse.collectors.infrastructure import collect_infrastructure

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

        collect.side_effect = fake_collect
        client = TestClient(app)
        res = client.get("/")
        assert res.status_code == 200


def test_requires_auth_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_AUTH_USERNAME", "admin")
    monkeypatch.setenv("WEB_AUTH_PASSWORD", "s3cret")
    monkeypatch.delenv("WEB_METRICS_TOKEN", raising=False)
    get_settings.cache_clear()

    with patch("validator_pulse.web.collect_pulse") as collect, patch(
        "validator_pulse.web.get_or_collect_pulse"
    ) as get_or:
        from validator_pulse.models import PulseSnapshot
        from validator_pulse.scoring import aggregate_fleet_metrics
        from validator_pulse.collectors.demo import (
            build_demo_consensus,
            build_demo_infrastructure,
            build_demo_validators,
        )
        from validator_pulse.collectors.infrastructure import collect_infrastructure

        consensus = build_demo_consensus()
        infra = build_demo_infrastructure(collect_infrastructure())
        validators = build_demo_validators([1], consensus, infra)
        snapshot = PulseSnapshot(
            collected_at="2026-01-01T00:00:00+00:00",
            demo_mode=True,
            verdict={"status": "healthy", "answer": "Yes", "summary": "ok"},
            validators=validators,
            consensus=consensus,
            infrastructure=infra,
            metrics=aggregate_fleet_metrics(validators),
        )

        async def fake(*_a, **_k):
            return snapshot

        collect.side_effect = fake
        get_or.side_effect = fake
        client = TestClient(app)

        denied = client.get("/")
        assert denied.status_code == 401
        assert "WWW-Authenticate" in denied.headers
        assert "password" not in denied.text.lower()
        assert "s3cret" not in denied.text

        for path, method in (
            ("/api/status", "get"),
            ("/api/metrics", "get"),
            ("/api/collect", "post"),
            ("/api/alerts/test", "post"),
        ):
            res = getattr(client, method)(path)
            assert res.status_code == 401, path

        ok = client.get("/", headers=_basic("admin", "s3cret"))
        assert ok.status_code == 200

        status = client.get("/api/status", headers=_basic("admin", "s3cret"))
        assert status.status_code == 200

        metrics = client.get("/api/metrics", headers=_basic("admin", "s3cret"))
        assert metrics.status_code == 200
        assert "validator_effectiveness_score" in metrics.text

        bad = client.get("/", headers=_basic("admin", "wrong"))
        assert bad.status_code == 401
        assert "s3cret" not in bad.text
        assert "wrong" not in bad.text


def test_metrics_token_allows_scrape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_AUTH_USERNAME", "admin")
    monkeypatch.setenv("WEB_AUTH_PASSWORD", "s3cret")
    monkeypatch.setenv("WEB_METRICS_TOKEN", "scrape-token")
    get_settings.cache_clear()

    with patch("validator_pulse.web.get_or_collect_pulse") as get_or:
        from validator_pulse.models import PulseSnapshot
        from validator_pulse.scoring import aggregate_fleet_metrics
        from validator_pulse.collectors.demo import (
            build_demo_consensus,
            build_demo_infrastructure,
            build_demo_validators,
        )
        from validator_pulse.collectors.infrastructure import collect_infrastructure

        consensus = build_demo_consensus()
        infra = build_demo_infrastructure(collect_infrastructure())
        validators = build_demo_validators([1], consensus, infra)
        snapshot = PulseSnapshot(
            collected_at="2026-01-01T00:00:00+00:00",
            demo_mode=True,
            verdict={"status": "healthy", "answer": "Yes", "summary": "ok"},
            validators=validators,
            consensus=consensus,
            infrastructure=infra,
            metrics=aggregate_fleet_metrics(validators),
        )

        async def fake(*_a, **_k):
            return snapshot

        get_or.side_effect = fake
        client = TestClient(app)

        assert client.get("/api/metrics").status_code == 401
        assert (
            client.get(
                "/api/metrics", headers={"Authorization": "Bearer scrape-token"}
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/api/metrics", headers={"X-Metrics-Token": "scrape-token"}
            ).status_code
            == 200
        )
        assert client.get("/api/metrics?token=scrape-token").status_code == 200
        # Token must not unlock the dashboard.
        assert (
            client.get("/", headers={"Authorization": "Bearer scrape-token"}).status_code
            == 401
        )


def test_metrics_token_only_without_basic_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_AUTH_USERNAME", "")
    monkeypatch.setenv("WEB_AUTH_PASSWORD", "")
    monkeypatch.setenv("WEB_METRICS_TOKEN", "scrape-only")
    get_settings.cache_clear()

    with patch("validator_pulse.web.collect_pulse") as collect, patch(
        "validator_pulse.web.get_or_collect_pulse"
    ) as get_or:
        from validator_pulse.models import PulseSnapshot
        from validator_pulse.scoring import aggregate_fleet_metrics
        from validator_pulse.collectors.demo import (
            build_demo_consensus,
            build_demo_infrastructure,
            build_demo_validators,
        )
        from validator_pulse.collectors.infrastructure import collect_infrastructure

        consensus = build_demo_consensus()
        infra = build_demo_infrastructure(collect_infrastructure())
        validators = build_demo_validators([1], consensus, infra)
        snapshot = PulseSnapshot(
            collected_at="2026-01-01T00:00:00+00:00",
            demo_mode=True,
            verdict={"status": "healthy", "answer": "Yes", "summary": "ok"},
            validators=validators,
            consensus=consensus,
            infrastructure=infra,
            metrics=aggregate_fleet_metrics(validators),
        )

        async def fake(*_a, **_k):
            return snapshot

        collect.side_effect = fake
        get_or.side_effect = fake
        client = TestClient(app)
        assert client.get("/").status_code == 200
        assert client.get("/api/metrics").status_code == 401
        assert client.get("/api/metrics?token=scrape-only").status_code == 200


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

    mini = FastAPI()
    settings = Settings(web_auth_username="u", web_auth_password="p")

    @mini.get("/ping")
    def ping():
        return {"ok": True}

    mini.add_middleware(WebAuthMiddleware, settings_factory=lambda: settings)
    client = TestClient(mini)
    assert client.get("/ping").status_code == 401
    assert client.get("/ping", headers=_basic("u", "p")).status_code == 200
