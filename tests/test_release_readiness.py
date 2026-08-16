"""End-to-end demo checks for every shipped chain, alerts, metrics, and HTTP APIs."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from validator_pulse.alerts import configured_channels, dispatch_alert, evaluate_alerts
from validator_pulse.chains import get_adapter, list_implemented_chains
from validator_pulse.chains.base import UnsupportedChainError
from validator_pulse.config import Settings, get_settings
from validator_pulse.metrics import to_prometheus
from validator_pulse.models import AlertEvent
from validator_pulse.pulse import collect_pulse
from validator_pulse.store import get_snapshot
from validator_pulse.web import app

# chain, extra Settings kwargs, token, base unit, decimals, parachain_id
_CHAIN_CASES: list[tuple[str, dict, str, str, int, int | None]] = [
    ("ethereum", {}, "ETH", "gwei", 9, None),
    ("polkadot", {"parachain_id": 2006}, "ASTR", "planck", 18, 2006),
    ("polkadot-relay", {}, "DOT", "planck", 10, None),
    ("cosmos", {}, "ATOM", "uatom", 6, None),
    ("cosmos", {"cosmos_profile": "celestia"}, "TIA", "utia", 6, None),
    ("solana", {}, "SOL", "lamports", 9, None),
    ("near", {}, "NEAR", "yoctoNEAR", 24, None),
    ("cardano", {}, "ADA", "lovelace", 6, None),
    ("tezos", {}, "XTZ", "mutez", 6, None),
    ("algorand", {}, "ALGO", "microAlgos", 6, None),
    ("bsc", {}, "BNB", "wei", 18, None),
    ("aptos", {}, "APT", "octas", 8, None),
    ("sui", {}, "SUI", "MIST", 9, None),
    ("monad", {}, "MON", "wei", 18, None),
    ("avalanche", {}, "AVAX", "nAVAX", 9, None),
    ("mina", {}, "MINA", "nanomina", 9, None),
    ("multiversx", {}, "EGLD", "wei", 18, None),
    ("ton", {}, "GRAM", "nanoton", 9, None),
]


def _case_ids() -> list[str]:
    ids: list[str] = []
    for chain, extra, *_ in _CHAIN_CASES:
        profile = extra.get("cosmos_profile")
        if profile:
            ids.append(f"{chain}-{profile}")
        elif chain == "polkadot" and extra.get("parachain_id") == 2006:
            ids.append("polkadot-astar")
        else:
            ids.append(chain)
    return ids


def _settings(chain: str, extra: dict | None = None) -> Settings:
    kwargs = {
        "chain": chain,
        "demo_mode": True,
        "fetch_operator_names": False,
        "parachain_id": None,
        "telegram_bot_token": None,
        "telegram_chat_id": None,
        "slack_webhook_url": None,
        "discord_webhook_url": None,
        "webhook_url": None,
        "pagerduty_routing_key": None,
    }
    kwargs.update(extra or {})
    return Settings(**kwargs)


def _reset_runtime() -> None:
    get_settings.cache_clear()
    import validator_pulse.store as store

    with store._lock:
        store._snapshot = None
        store._alert_history = []


def _collect(chain: str, extra: dict | None = None):
    return asyncio.run(collect_pulse(_settings(chain, extra), dispatch_alerts=False))


@pytest.fixture(autouse=True)
def _isolate_settings_and_store():
    _reset_runtime()
    yield
    _reset_runtime()


def test_catalog_covers_every_implemented_chain() -> None:
    covered = {chain for chain, extra, *_ in _CHAIN_CASES if chain != "polkadot-relay"}
    assert covered == set(list_implemented_chains())


@pytest.mark.parametrize(
    "chain,extra,symbol,base_unit,decimals,para_id",
    _CHAIN_CASES,
    ids=_case_ids(),
)
def test_demo_snapshot_shows_chain_data(
    chain: str,
    extra: dict,
    symbol: str,
    base_unit: str,
    decimals: int,
    para_id: int | None,
) -> None:
    settings = _settings(chain, extra)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    adapter = get_adapter(settings.chain)
    if hasattr(adapter, "configure"):
        adapter.configure(settings)

    assert snapshot.schema_version == 2
    assert snapshot.demo_mode is True
    assert snapshot.chain == adapter.name
    assert snapshot.chain_display_name == adapter.display_name
    assert snapshot.operator_label == adapter.operator_label
    assert snapshot.risk_kind == adapter.risk_kind
    assert snapshot.risk_label == adapter.risk_label
    assert snapshot.primary_duty_label == adapter.primary_duty_label
    assert snapshot.secondary_duty_label == adapter.secondary_duty_label
    assert snapshot.missed_duty_label == adapter.missed_duty_label
    assert snapshot.consensus_node_label == adapter.consensus_node_label
    assert snapshot.parachain_id == para_id
    assert snapshot.reward_token_symbol == symbol
    assert snapshot.reward_token_decimals == decimals
    assert snapshot.reward_token_base_unit == base_unit
    assert snapshot.verdict.status in {"healthy", "degraded", "critical", "unknown"}
    assert snapshot.verdict.answer
    assert snapshot.verdict.summary
    assert snapshot.consensus.status
    assert snapshot.infrastructure.status
    assert snapshot.metrics.effectiveness_score is not None
    assert snapshot.validators

    ids: set[str] = set()
    for op in snapshot.validators:
        assert op.operator_id
        assert op.status
        assert 0 <= op.effectiveness_score <= 100
        risk = op.risk_score if op.risk_score is not None else op.slashing_risk_score
        assert 0 <= risk <= 100
        assert op.risk_kind == adapter.risk_kind
        assert op.duties
        assert op.balance_base_units == op.balance_gwei
        assert op.rewards_base_units == op.rewards_gwei
        ids.add(op.operator_id)
    assert len(ids) == len(snapshot.validators)


@pytest.mark.parametrize(
    "chain,extra,symbol,base_unit,decimals,para_id",
    _CHAIN_CASES,
    ids=_case_ids(),
)
def test_demo_dashboard_and_api_render_chain(
    monkeypatch: pytest.MonkeyPatch,
    chain: str,
    extra: dict,
    symbol: str,
    base_unit: str,
    decimals: int,
    para_id: int | None,
) -> None:
    del base_unit, decimals
    monkeypatch.setenv("CHAIN", chain)
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("FETCH_OPERATOR_NAMES", "false")
    monkeypatch.setenv("PARACHAIN_ID", "" if para_id is None else str(para_id))
    monkeypatch.setenv("COSMOS_PROFILE", extra.get("cosmos_profile") or "cosmoshub")
    monkeypatch.setenv("WEB_AUTH_USERNAME", "")
    monkeypatch.setenv("WEB_AUTH_PASSWORD", "")
    monkeypatch.setenv("WEB_METRICS_TOKEN", "")
    get_settings.cache_clear()

    snapshot = asyncio.run(collect_pulse(get_settings(), dispatch_alerts=False))
    with TestClient(app) as client:
        page = client.get("/")
        status = client.get("/api/status")
        metrics = client.get("/api/metrics")

    assert page.status_code == 200
    html = page.text
    assert snapshot.chain_display_name in html
    assert "Demo mode" in html
    assert snapshot.primary_duty_label in html
    assert snapshot.risk_label in html
    if para_id is not None:
        assert f"para {para_id}" in html
        assert symbol in html
    else:
        assert "para 2006" not in html
    for op in snapshot.validators:
        assert op.operator_id in html or (op.operator_index is not None and f"#{op.operator_index}" in html)

    assert status.status_code == 200
    body = status.json()
    assert body["schema_version"] == 2
    assert body["chain"] == snapshot.chain
    assert body["reward_token_symbol"] == symbol
    assert body["demo_mode"] is True
    assert len(body["validators"]) == len(snapshot.validators)

    assert metrics.status_code == 200
    text = metrics.text
    assert "operator_effectiveness_score" in text
    assert "validator_effectiveness_score" in text
    assert f'chain="{snapshot.chain}"' in text


@pytest.mark.parametrize(
    "chain,extra,symbol,base_unit,decimals,para_id",
    _CHAIN_CASES,
    ids=_case_ids(),
)
def test_demo_alerts_use_chain_wording(
    chain: str,
    extra: dict,
    symbol: str,
    base_unit: str,
    decimals: int,
    para_id: int | None,
) -> None:
    del symbol, base_unit, decimals, para_id
    settings = _settings(chain, extra)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    alerts = evaluate_alerts(snapshot, settings)
    assert isinstance(alerts, list)
    for alert in alerts:
        assert alert.title
        assert alert.message
        assert alert.severity in {"info", "warning", "critical"}
        assert alert.source in {"validator", "consensus", "infrastructure", "system"}

    # Shared thresholds must use this chain's labels, not Ethereum leftovers.
    target = snapshot.validators[0]
    target.attestations.missed = max(target.attestations.missed, 5)
    target.effectiveness_score = 70
    target.risk_score = 80
    target.slashing_risk_score = 80
    forced = evaluate_alerts(snapshot, settings)
    titles = " ".join(a.title.lower() for a in forced)
    assert snapshot.primary_duty_label.lower() in titles
    assert snapshot.operator_label.lower() in titles
    assert snapshot.risk_label.lower() in titles
    assert "effectiveness" in titles


def test_demo_fleets_emit_some_native_alerts() -> None:
    produced = 0
    for chain, extra, *_ in _CHAIN_CASES:
        snapshot = asyncio.run(collect_pulse(_settings(chain, extra), dispatch_alerts=False))
        produced += len(evaluate_alerts(snapshot, _settings(chain, extra)))
    assert produced > 0


def test_ethereum_dashboard_hides_foreign_parachain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAIN", "ethereum")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("FETCH_OPERATOR_NAMES", "false")
    monkeypatch.setenv("PARACHAIN_ID", "2006")
    get_settings.cache_clear()
    snapshot = asyncio.run(collect_pulse(get_settings(), dispatch_alerts=False))
    assert snapshot.parachain_id is None
    assert snapshot.reward_token_symbol == "ETH"
    with TestClient(app) as client:
        html = client.get("/").text
    assert "para 2006" not in html
    assert "ASTR" not in html


def test_two_chains_in_env_are_rejected() -> None:
    with pytest.raises(UnsupportedChainError, match="ethereum,solana"):
        get_adapter("ethereum,solana")
    settings = _settings("ethereum,solana")
    with pytest.raises(UnsupportedChainError):
        asyncio.run(collect_pulse(settings, dispatch_alerts=False))


def test_prometheus_dual_emits_operator_and_legacy_series() -> None:
    snapshot = _collect("near")
    text = to_prometheus(snapshot)
    assert "operator_effectiveness_score" in text
    assert "validator_effectiveness_score" in text
    assert "operator_risk_score" in text
    assert 'chain="near"' in text
    for op in snapshot.validators:
        assert f'operator_id="{op.operator_id}"' in text


def test_all_alert_channels_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    posts: list[str] = []

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None, **kwargs):
            del json, kwargs
            posts.append(url)
            return SimpleNamespace(is_success=True, status_code=200)

    settings = Settings(
        chain="ethereum",
        demo_mode=True,
        fetch_operator_names=False,
        telegram_bot_token="tg-token",
        telegram_chat_id="123",
        slack_webhook_url="https://hooks.slack.test/x",
        discord_webhook_url="https://discord.test/api/webhooks/x",
        webhook_url="https://example.test/hook",
        pagerduty_routing_key="pd-key",
    )
    assert configured_channels(settings) == [
        "telegram",
        "slack",
        "discord",
        "webhook",
        "pagerduty",
    ]
    alert = AlertEvent(
        id="release-check",
        severity="warning",
        title="ValidatorPulse channel check",
        message="Dispatch path is reachable.",
        source="system",
        created_at="2026-08-16T00:00:00+00:00",
        channels=configured_channels(settings),
    )
    with patch("validator_pulse.alerts.dispatch.httpx.AsyncClient", _FakeClient):
        results = asyncio.run(dispatch_alert(settings, alert))
    assert [r.channel for r in results] == list(alert.channels)
    assert all(r.ok for r in results)
    assert len(posts) == 5
    assert any("api.telegram.org" in url for url in posts)
    assert any("hooks.slack.test" in url for url in posts)
    assert any("discord.test" in url for url in posts)
    assert any("example.test/hook" in url for url in posts)
    assert any("pagerduty.com" in url for url in posts)


def test_alerts_test_endpoint_and_collect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAIN", "solana")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("FETCH_OPERATOR_NAMES", "false")
    monkeypatch.setenv("WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setenv("WEB_AUTH_USERNAME", "")
    monkeypatch.setenv("WEB_AUTH_PASSWORD", "")
    get_settings.cache_clear()

    posts: list[str] = []

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None, **kwargs):
            del json, kwargs
            posts.append(url)
            return SimpleNamespace(is_success=True, status_code=200)

    with patch("validator_pulse.alerts.dispatch.httpx.AsyncClient", _FakeClient):
        with TestClient(app) as client:
            empty = Settings(
                chain="solana",
                demo_mode=True,
                fetch_operator_names=False,
                webhook_url=None,
            )
            with patch("validator_pulse.web.get_settings", return_value=empty):
                missing = client.post("/api/alerts/test")
            ok = client.post("/api/alerts/test")
            collected = client.post("/api/collect")

    assert missing.status_code == 400
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["ok"] is True
    assert payload["results"][0]["channel"] == "webhook"
    assert posts
    assert collected.status_code == 200
    assert collected.json()["ok"] is True
    assert collected.json()["snapshot"]["chain"] == "solana"
    stored = get_snapshot()
    assert stored is not None
    assert stored.chain == "solana"
