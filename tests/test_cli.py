from __future__ import annotations

import pytest

from validator_pulse import __version__
from validator_pulse.__main__ import (
    _bind_error_message,
    _reload_enabled,
    parse_args,
)


def test_help_prints_usage_and_exits(capsys) -> None:
    with pytest.raises(SystemExit) as exited:
        parse_args(["--help"])
    assert exited.value.code == 0
    out = capsys.readouterr().out
    assert "Self-hosted validator monitor" in out
    assert "DEMO_MODE" in out
    assert ".env.local" in out
    assert "--port" in out
    assert "--host" in out


def test_version_prints_package_version(capsys) -> None:
    with pytest.raises(SystemExit) as exited:
        parse_args(["--version"])
    assert exited.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_parse_host_and_port() -> None:
    args = parse_args(["--host", "0.0.0.0", "--port", "3001", "--reload"])
    assert args.host == "0.0.0.0"
    assert args.port == 3001
    assert args.reload is True


def test_bind_error_mentions_another_port() -> None:
    text = _bind_error_message("127.0.0.1", 3000)
    assert "Port 3000 is already in use" in text
    assert "validator-pulse --port 3001" in text
    assert "PORT=3001" in text


def test_reload_off_for_packaged_install(monkeypatch) -> None:
    monkeypatch.delenv("UVICORN_RELOAD", raising=False)
    assert _reload_enabled("127.0.0.1", source_install=False) is False
    monkeypatch.setenv("UVICORN_RELOAD", "true")
    assert _reload_enabled("127.0.0.1", source_install=False) is True
