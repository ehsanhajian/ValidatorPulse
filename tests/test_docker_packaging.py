from __future__ import annotations

from pathlib import Path

from validator_pulse.__main__ import _reload_enabled

ROOT = Path(__file__).resolve().parents[1]


def test_compose_publishes_caddy_not_the_app() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "3000:3000" not in compose
    assert "validator-pulse:3000" not in compose
    assert "expose:" in compose
    assert '"3000"' in compose
    assert "caddy:" in compose
    assert "127.0.0.1:${CADDY_METRICS_PORT:-9091}:9091" in compose
    assert "${CADDY_HTTP_BIND:-127.0.0.1}:${CADDY_HTTP_PORT:-80}:80" in compose
    assert "Caddyfile.restrict" not in compose.split("CADDYFILE:-")[0]
    assert "${CADDYFILE:-Caddyfile}" in compose


def test_lab_caddyfile_proxies_app_and_isolates_metrics() -> None:
    text = (ROOT / "deploy/caddy/Caddyfile").read_text()
    assert "reverse_proxy validator-pulse:3000" in text
    assert "remote_ip" not in text
    assert "http://:9091" in text
    assert "handle /api/metrics*" in text


def test_restrict_caddyfile_is_fail_closed() -> None:
    text = (ROOT / "deploy/caddy/Caddyfile.restrict").read_text()
    assert "remote_ip {$CADDY_ALLOW_IPS:255.255.255.255}" in text
    assert 'respond "Forbidden" 403' in text
    assert "reverse_proxy validator-pulse:3000" in text
    assert "http://:9091" in text


def test_compose_env_sample_has_no_secrets() -> None:
    text = (ROOT / "compose.env.example").read_text()
    assert "CHAIN=ethereum" in text
    assert "DEMO_MODE=true" in text
    assert "CADDYFILE=Caddyfile" in text
    assert "Caddyfile.restrict" in text
    lowered = text.lower()
    for needle in ("sk-", "xoxb-", "ghp_", "-----begin"):
        assert needle not in lowered


def test_dockerfile_runs_as_non_root_without_reload() -> None:
    text = (ROOT / "Dockerfile").read_text()
    assert "USER vp" in text
    assert "UVICORN_RELOAD=false" in text
    assert "HOST=0.0.0.0" in text
    assert 'CMD ["python", "-m", "validator_pulse"]' in text


def test_reload_defaults_to_loopback_only(monkeypatch) -> None:
    monkeypatch.delenv("UVICORN_RELOAD", raising=False)
    assert _reload_enabled("127.0.0.1") is True
    assert _reload_enabled("0.0.0.0") is False
    monkeypatch.setenv("UVICORN_RELOAD", "false")
    assert _reload_enabled("127.0.0.1") is False
    monkeypatch.setenv("UVICORN_RELOAD", "true")
    assert _reload_enabled("0.0.0.0") is True
