from __future__ import annotations

import argparse
import errno
import logging
import os
import socket
import sys
import textwrap
from pathlib import Path

import uvicorn

from validator_pulse import __version__
from validator_pulse.config import get_settings

_ENV_EXAMPLE = (
    "https://raw.githubusercontent.com/ehsanhajian/ValidatorPulse/main/.env.example"
)
_DOCS = "https://github.com/ehsanhajian/ValidatorPulse#installation"


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validator-pulse",
        description="Self-hosted validator monitor (dashboard, Prometheus, alerts).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            f"""\
            Examples:
              CHAIN=ethereum DEMO_MODE=true validator-pulse
              validator-pulse --port 3001

            Reads .env.local then .env from the current directory. Sample:
              curl -fsSL -o .env.local {_ENV_EXAMPLE}

            Then open http://127.0.0.1:3000  (or the --host/--port you set).
            Docs: {_DOCS}
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
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    host = args.host or settings.host
    port = args.port if args.port is not None else settings.port
    reload = True if args.reload else _reload_enabled(host)

    if _port_in_use(host, port):
        print(_bind_error_message(host, port), file=sys.stderr)
        raise SystemExit(1)

    print(
        f"ValidatorPulse {__version__}  http://{host}:{port}  "
        f"(CHAIN={settings.chain} DEMO_MODE={settings.demo_mode})",
        file=sys.stderr,
    )
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
