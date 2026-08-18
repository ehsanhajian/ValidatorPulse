from __future__ import annotations

import argparse
import errno
import logging
import os
import socket
import sys
import textwrap
from importlib.resources import files
from pathlib import Path

import uvicorn

from validator_pulse import __version__
from validator_pulse.config import get_settings

_DOCS = "https://github.com/ehsanhajian/ValidatorPulse#installation"
_LANDING = "https://ehsanhajian.github.io/ValidatorPulse/"
_ENV_LOCAL = ".env.local"
_ENV_FILE = ".env"


def _reload_enabled(host: str, *, source_install: bool | None = None) -> bool:
    raw = os.environ.get("UVICORN_RELOAD")
    if raw is not None and raw.strip() != "":
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if source_install is None:
        source_install = _is_source_install()
    if not source_install:
        return False
    return (host or "").strip().lower() in {"127.0.0.1", "localhost", "::1"}


def _is_source_install() -> bool:
    parts = Path(__file__).resolve().parts
    return "site-packages" not in parts and "dist-packages" not in parts


def _port_in_use(host: str, port: int) -> bool:
    probe_host = "127.0.0.1" if (host or "").strip() in {"0.0.0.0", "::", ""} else host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            return sock.connect_ex((probe_host, port)) == 0
    except OSError:
        return False


def _bind_error_message(host: str, port: int) -> str:
    display = "127.0.0.1" if host in {"0.0.0.0", ""} else host
    return (
        f"Port {port} is already in use on {display}.\n"
        f"Stop the other process, or pick another port:\n"
        f"  validator-pulse --port {port + 1}\n"
        f"  PORT={port + 1} validator-pulse"
    )


def _bundled_env_example() -> str:
    return files("validator_pulse.data").joinpath("env.example").read_text(encoding="utf-8")


def _cwd_env_paths() -> tuple[Path, Path]:
    cwd = Path.cwd()
    return cwd / _ENV_LOCAL, cwd / _ENV_FILE


def _existing_config_path() -> Path | None:
    local, env = _cwd_env_paths()
    if local.is_file():
        return local
    if env.is_file():
        return env
    return None


def startup_guide(*, host: str, port: int, chain: str, demo_mode: bool) -> str:
    config = _existing_config_path()
    cwd = Path.cwd()
    if config is None:
        config_line = (
            f"  Config     none in {cwd} — using defaults (DEMO_MODE={demo_mode})\n"
            f"  Create     validator-pulse init     # writes {cwd / _ENV_LOCAL}"
        )
    else:
        config_line = f"  Config     {config}"
    return (
        f"ValidatorPulse {__version__}\n"
        f"\n"
        f"  Dashboard  http://{host}:{port}\n"
        f"{config_line}\n"
        f"  Chain      {chain}   DEMO_MODE={demo_mode}\n"
        f"  Docs       {_DOCS}\n"
        f"  Landing    {_LANDING}\n"
        f"\n"
        f"Edit {_ENV_LOCAL} in this directory, then restart. One CHAIN per process."
    )


def init_env(*, force: bool = False, dest: Path | None = None) -> Path:
    target = dest or (Path.cwd() / _ENV_LOCAL)
    if target.exists() and not force:
        raise FileExistsError(
            f"{target} already exists. Re-run with: validator-pulse init --force"
        )
    target.write_text(_bundled_env_example(), encoding="utf-8")
    return target


def init_success_message(path: Path) -> str:
    return (
        f"Wrote {path}\n"
        f"\n"
        f"Next:\n"
        f"  1. Edit that file: set CHAIN, DEMO_MODE=false, and your RPC + identifiers\n"
        f"  2. Start:  validator-pulse\n"
        f"  3. Open:   http://127.0.0.1:3000\n"
        f"\n"
        f"Docs: {_DOCS}\n"
        f"Leave DEMO_MODE=true for a first look without a node."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validator-pulse",
        description="Self-hosted validator monitor (dashboard, Prometheus, alerts).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            f"""\
            After pip install, create config in the current directory:

              validator-pulse init
              validator-pulse

            Then edit .env.local (CHAIN, RPC URL, identifiers). Docs:
              {_DOCS}
            """
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Bind address (default: HOST env or 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: PORT env or 3000)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Auto-reload on code changes (development only)",
    )
    sub = parser.add_subparsers(dest="command")
    init_p = sub.add_parser(
        "init",
        help=f"Write {_ENV_LOCAL} in the current directory (sample config)",
    )
    init_p.add_argument(
        "--force",
        action="store_true",
        help=f"Overwrite an existing {_ENV_LOCAL}",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.command == "init":
        try:
            path = init_env(force=bool(getattr(args, "force", False)))
        except FileExistsError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        print(init_success_message(path))
        return

    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    host = args.host or settings.host
    port = args.port if args.port is not None else settings.port
    reload = True if args.reload else _reload_enabled(host)

    print(startup_guide(host=host, port=port, chain=settings.chain, demo_mode=settings.demo_mode))

    if _port_in_use(host, port):
        print(_bind_error_message(host, port), file=sys.stderr)
        raise SystemExit(1)

    try:
        uvicorn.run(
            "validator_pulse.web:app",
            host=host,
            port=port,
            reload=reload,
        )
    except OSError as exc:
        in_use = {errno.EADDRINUSE}
        if hasattr(errno, "WSAEADDRINUSE"):
            in_use.add(errno.WSAEADDRINUSE)
        if exc.errno in in_use:
            print(_bind_error_message(host, port), file=sys.stderr)
            raise SystemExit(1) from exc
        raise


if __name__ == "__main__":
    main()
