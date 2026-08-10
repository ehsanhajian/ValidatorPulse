from __future__ import annotations

import asyncio
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from validator_pulse.config import Settings
from validator_pulse.http_client import (
    RpcHttpConfig,
    RpcTransportError,
    bind_rpc_http_config,
    classify_transport_error,
    create_async_client,
    format_transport_error,
    normalize_rpc_url,
    parse_rpc_url,
    probe_rpc_endpoint,
    reset_rpc_http_config,
)


def test_normalize_adds_http_scheme_and_keeps_port() -> None:
    assert normalize_rpc_url("127.0.0.1:8443") == "http://127.0.0.1:8443"
    assert (
        normalize_rpc_url("https://beacon.example.com:8443/v1/")
        == "https://beacon.example.com:8443/v1"
    )


def test_parse_https_nonstandard_port() -> None:
    parsed = parse_rpc_url("https://rpc.example.com:8443/substrate")
    assert parsed.scheme == "https"
    assert parsed.host == "rpc.example.com"
    assert parsed.port == 8443
    assert parsed.uses_tls is True
    assert parsed.path == "/substrate"
    assert parsed.base_url == "https://rpc.example.com:8443/substrate"


def test_parse_default_https_port() -> None:
    parsed = parse_rpc_url("https://beacon.example.com")
    assert parsed.port == 443
    assert parsed.uses_tls is True


def test_rpc_http_config_from_settings_verify_paths() -> None:
    default = RpcHttpConfig.from_settings(Settings())
    assert default.verify is True
    assert default.insecure is False

    insecure = RpcHttpConfig.from_settings(Settings(rpc_tls_insecure=True))
    assert insecure.verify is False
    assert insecure.insecure is True

    ca = RpcHttpConfig.from_settings(
        Settings(rpc_tls_ca_bundle="/tmp/ca.pem", rpc_tls_verify=True)
    )
    assert ca.verify == "/tmp/ca.pem"


def test_create_async_client_honors_bound_config() -> None:
    token = bind_rpc_http_config(RpcHttpConfig(verify=False, timeout=3.0, insecure=True))
    try:

        async def run() -> None:
            client = create_async_client()
            assert client is not None
            await client.aclose()

        asyncio.run(run())
    finally:
        reset_rpc_http_config(token)


def test_classify_tls_vs_connection() -> None:
    ssl_exc = ssl.SSLCertVerificationError("certificate verify failed")
    wrapped = httpx.ConnectError("TLS failure", request=MagicMock())
    wrapped.__cause__ = ssl_exc
    kind, message = classify_transport_error(wrapped)
    assert kind == "tls"
    assert "TLS" in message

    refused = httpx.ConnectError(
        "All connection attempts failed", request=MagicMock()
    )
    kind2, message2 = classify_transport_error(refused)
    assert kind2 == "connection"
    assert "Connection failed" in message2
    assert "TLS" not in message2

    timed = httpx.ConnectTimeout("timed out", request=MagicMock())
    kind3, message3 = classify_transport_error(timed)
    assert kind3 == "timeout"
    assert "timed out" in message3.lower() or "Timeout" in message3 or "timed" in message3.lower()


def test_format_transport_error_preserves_rpc_transport_error() -> None:
    err = RpcTransportError("tls", "TLS error: hostname mismatch")
    assert format_transport_error(err) == "TLS error: hostname mismatch"


def test_probe_rpc_endpoint_maps_tls_failure() -> None:
    async def run() -> None:
        ssl_exc = ssl.SSLCertVerificationError("self signed certificate")
        connect = httpx.ConnectError("error", request=MagicMock())
        connect.__cause__ = ssl_exc

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=connect)
        mock_client.aclose = AsyncMock()

        with patch(
            "validator_pulse.http_client.create_async_client",
            return_value=mock_client,
        ):
            with pytest.raises(RpcTransportError) as caught:
                await probe_rpc_endpoint("https://rpc.example.com:8443")
            assert caught.value.kind == "tls"
            assert "TLS" in str(caught.value)

    asyncio.run(run())


def test_probe_rpc_endpoint_maps_connection_refused() -> None:
    async def run() -> None:
        connect = httpx.ConnectError(
            "All connection attempts failed", request=MagicMock()
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=connect)
        mock_client.aclose = AsyncMock()

        with patch(
            "validator_pulse.http_client.create_async_client",
            return_value=mock_client,
        ):
            with pytest.raises(RpcTransportError) as caught:
                await probe_rpc_endpoint("http://127.0.0.1:1")
            assert caught.value.kind == "connection"

    asyncio.run(run())


def test_ethereum_consensus_surfaces_tls_error_message() -> None:
    from validator_pulse.collectors.beacon import collect_consensus

    async def run() -> None:
        ssl_exc = ssl.SSLCertVerificationError("certificate verify failed")
        connect = httpx.ConnectError("error", request=MagicMock())
        connect.__cause__ = ssl_exc
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=connect)
        mock_client.aclose = AsyncMock()

        with patch(
            "validator_pulse.http_client.create_async_client",
            return_value=mock_client,
        ):
            health = await collect_consensus("https://beacon.example.com:8443")
            assert health.beacon_reachable is False
            assert health.last_error
            assert "TLS" in health.last_error

    asyncio.run(run())


def test_substrate_consensus_surfaces_connection_error_message() -> None:
    from validator_pulse.chains.polkadot.rpc import collect_substrate_consensus

    async def run() -> None:
        connect = httpx.ConnectError(
            "All connection attempts failed", request=MagicMock()
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=connect)
        mock_client.post = AsyncMock(side_effect=connect)
        mock_client.aclose = AsyncMock()

        with patch(
            "validator_pulse.http_client.create_async_client",
            return_value=mock_client,
        ):
            health = await collect_substrate_consensus("https://rpc.example.com:9934")
            assert health.beacon_reachable is False
            assert health.last_error
            assert "Connection failed" in health.last_error
            assert "TLS" not in health.last_error

    asyncio.run(run())
