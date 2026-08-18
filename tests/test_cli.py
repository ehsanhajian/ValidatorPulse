from __future__ import annotations

from pathlib import Path

import pytest

from validator_pulse import __version__
from validator_pulse.__main__ import (
    _bind_error_message,
    _bundled_env_example,
    _reload_enabled,
    init_env,
    init_success_message,
    parse_args,
    startup_guide,
)

REPO = Path(__file__).resolve().parents[1]


def test_help_prints_usage_and_exits(capsys) -> None:
    with pytest.raises(SystemExit) as exited:
        parse_args(["--help"])
    assert exited.value.code == 0
    out = capsys.readouterr().out
    assert "Self-hosted validator monitor" in out
    assert "validator-pulse init" in out
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
    assert args.command is None


def test_parse_init() -> None:
    args = parse_args(["init", "--force"])
    assert args.command == "init"
    assert args.force is True


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


def test_bundled_env_matches_repo_example() -> None:
    assert _bundled_env_example() == (REPO / ".env.example").read_text(encoding="utf-8")


def test_init_writes_env_local(tmp_path: Path) -> None:
    dest = tmp_path / ".env.local"
    written = init_env(dest=dest)
    assert written == dest
    text = dest.read_text(encoding="utf-8")
    assert text.startswith("# One CHAIN per process")
    assert "CHAIN=ethereum" in text
    with pytest.raises(FileExistsError):
        init_env(dest=dest)
    init_env(dest=dest, force=True)
    assert "validator-pulse" in init_success_message(dest)
    assert str(dest) in init_success_message(dest)


def test_startup_guide_tells_pip_users_where_to_put_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    text = startup_guide(host="127.0.0.1", port=3000, chain="ethereum", demo_mode=True)
    assert "http://127.0.0.1:3000" in text
    assert "validator-pulse init" in text
    assert ".env.local" in text
    assert "github.com/ehsanhajian/ValidatorPulse#installation" in text
    (tmp_path / ".env.local").write_text("CHAIN=solana\n", encoding="utf-8")
    found = startup_guide(host="127.0.0.1", port=3000, chain="solana", demo_mode=False)
    assert str(tmp_path / ".env.local") in found
    assert "validator-pulse init" not in found
