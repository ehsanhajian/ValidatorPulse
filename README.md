# ValidatorPulse

**Is my validator operating correctly?**

ValidatorPulse is a FastAPI service that answers that question with a live dashboard, Prometheus metrics, and multi-channel alerts. It watches operator duties, consensus health, and host infrastructure—so you catch downtime and penalties before they escalate.

**Non-goal:** it does not scan external security surfaces.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env.local
python -m validator_pulse
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000). With `DEMO_MODE=true` (default), the app simulates duty data so you can explore the UI without a node.

## What it monitors

| Area | Signals |
| --- | --- |
| **Operator** | Chain-specific duties (attestations, collations, chunks, blocks, …), missed work, rewards, effectiveness, operational/penalty risk |
| **Consensus** | Node reachability, sync distance, finality, peers |
| **Infrastructure** | CPU, memory, disk usage & latency, network, clock drift |

Adapters supply display labels (`risk_label`, duty names, consensus node name). Shared scoring, alerts, metrics, and the dashboard stay chain-agnostic.

## Chains

| `CHAIN` | Status | Notes |
| --- | --- | --- |
| `ethereum` | Implemented | Beacon validators (index / BLS pubkey) |
| `polkadot` | Implemented | Parachain collators (SS58); relay validators tracked in [#11](https://github.com/ehsanhajian/ValidatorPulse/issues/11) |
| `cosmos` | Planned | [#5](https://github.com/ehsanhajian/ValidatorPulse/issues/5) (includes Celestia via Bech32 profiles) |
| `solana` | Planned | [#6](https://github.com/ehsanhajian/ValidatorPulse/issues/6) |
| `near` | Planned | [#23](https://github.com/ehsanhajian/ValidatorPulse/issues/23) |
| `cardano` | Planned | [#24](https://github.com/ehsanhajian/ValidatorPulse/issues/24) |
| `tezos` | Planned | [#25](https://github.com/ehsanhajian/ValidatorPulse/issues/25) |
| `algorand` | Planned | [#26](https://github.com/ehsanhajian/ValidatorPulse/issues/26) |
| `bsc` | Planned | [#27](https://github.com/ehsanhajian/ValidatorPulse/issues/27) |
| `aptos` | Planned | [#28](https://github.com/ehsanhajian/ValidatorPulse/issues/28) |
| `sui` | Planned | [#29](https://github.com/ehsanhajian/ValidatorPulse/issues/29) |
| `monad` | Planned | [#30](https://github.com/ehsanhajian/ValidatorPulse/issues/30) |
| `avalanche` | Planned | [#31](https://github.com/ehsanhajian/ValidatorPulse/issues/31) |
| `mina` | Planned | [#32](https://github.com/ehsanhajian/ValidatorPulse/issues/32) |
| `multiversx` | Planned | [#33](https://github.com/ehsanhajian/ValidatorPulse/issues/33) |
| `ton` | Planned | [#34](https://github.com/ehsanhajian/ValidatorPulse/issues/34) |

Shared models were generalized for heterogeneous L1s in [#35](https://github.com/ehsanhajian/ValidatorPulse/issues/35). Packaging (Docker / Caddy) is tracked in [#9](https://github.com/ehsanhajian/ValidatorPulse/issues/9).

```env
CHAIN=ethereum
```

### Ethereum

![Ethereum dashboard (demo mode)](docs/images/dashboard-ethereum.png)

Validators are identified by **index** and/or **BLS pubkey**—not by an execution wallet (`0x` + 40 hex).

| Identifier | Env var | Example |
| --- | --- | --- |
| Validator index | `VALIDATOR_INDICES` | `123456,789012` |
| BLS pubkey | `VALIDATOR_PUBKEYS` | `0x` + 96 hex chars |

```env
CHAIN=ethereum
DEMO_MODE=false
BEACON_API_URL=http://127.0.0.1:5052
VALIDATOR_INDICES=123456,789012
```

Live mode tracks attestation and proposal duties across polls. Rolling rewards use signed Beacon consensus rewards (attestations, proposals, sync committee)—never `balance − effective_balance`. The UI marks the window **partial** while warming up. Execution tips and MEV are excluded. Demo mode simulates a full window without a beacon node.

**Display names** (fail-soft, cached ~1h): beaconcha.in → Rated operator/pool mapping (`RATED_API_KEY`) → ENS on the withdrawal address (`ENS_LOOKUP_ENABLED=true`) → recent proposal graffiti → index/pubkey fallback. Set `FETCH_OPERATOR_NAMES=false` to disable lookups.

### Polkadot / parachain collators

![Polkadot collator dashboard (demo mode, Astar / ASTR)](docs/images/dashboard-polkadot.png)

| Identifier | Env var | Example |
| --- | --- | --- |
| Substrate HTTP RPC | `SUBSTRATE_RPC_URL` | `http://127.0.0.1:9933` |
| Collator SS58 | `COLLATOR_ADDRESSES` | `5Grw…,5FHn…` |
| Parachain id | `PARACHAIN_ID` | `2006` (Astar → ASTR) |
| Token overrides | `REWARD_TOKEN_SYMBOL` / `REWARD_TOKEN_DECIMALS` | when not in the built-in map |

```env
CHAIN=polkadot
DEMO_MODE=false
SUBSTRATE_RPC_URL=http://127.0.0.1:9933
COLLATOR_ADDRESSES=5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY
PARACHAIN_ID=2006
```

| `PARACHAIN_ID` | Network | Token |
| --- | --- | --- |
| _(unset)_ | Polkadot default | DOT |
| 1000 | Asset Hub | DOT |
| 2000 | Acala | ACA |
| 2004 | Moonbeam | GLMR |
| 2006 | Astar | ASTR |
| 2030 | Bifrost | BNC |
| 2034 | Hydration | HDX |
| 2035 | Phala | PHA |
| 2046 | Manta | MANTA |
| 2007 | Shiden | SDN |
| 2023 | Moonriver | MOVR |

### Add a chain plugin

1. Implement `ChainAdapter` in `validator_pulse/chains/<name>/adapter.py`
2. Register it in `validator_pulse/chains/registry.py`
3. Add settings to `Settings` / `.env.example`
4. Keep infrastructure, alerting, and Prometheus outside the adapter

```python
class ChainAdapter(Protocol):
    name: str
    display_name: str
    operator_label: str
    risk_kind: str          # slashing | kickout | jail | … 
    risk_label: str         # UI / alert wording
    primary_duty_label: str
    secondary_duty_label: str
    missed_duty_label: str
    consensus_node_label: str

    def is_demo(self, settings: Settings) -> bool: ...
    async def collect(...) -> ChainCollection: ...
```

## Operator model & API migration

`GET /api/status` snapshots use `schema_version: 2`. Canonical fields:

| Concept | Canonical | Compatibility aliases |
| --- | --- | --- |
| Identity | `operator_id` (string) | optional `operator_index` / legacy `index` |
| Balances & rewards | `*_base_units` + token metadata on the snapshot | `*_gwei` (same integer; name is historical) |
| Duties | `duties[]` with category + label | `attestations` / `proposals` |
| Risk | `risk_score` + `risk_kind` | `slashing_risk_score` |
| Protocol incidents | `protocol_events[]` | — |

`OperatorStats` is an alias of `ValidatorStats`. Ethereum and Polkadot behavior is unchanged for existing clients that still read the aliases.

Prometheus dual-emits:

- **Legacy:** `validator_*`, `validator_balance_gwei`, `validator_rewards_gwei`, …
- **Preferred:** `operator_*` with `chain` / `operator_id` (and `risk_kind` / token labels where relevant)

Prefer the `operator_*` series for new scrapes; legacy names remain so existing dashboards do not break abruptly.

## Configuration reference

Restart after changing `.env.local` (or use reload via `python -m validator_pulse`).

| Variable | Purpose | Default |
| --- | --- | --- |
| `CHAIN` | Active adapter | `ethereum` |
| `BEACON_API_URL` | Ethereum consensus HTTP API | unset |
| `VALIDATOR_INDICES` / `VALIDATOR_PUBKEYS` | Ethereum operators | `1,2,3` / empty |
| `SUBSTRATE_RPC_URL` / `COLLATOR_ADDRESSES` | Polkadot collators | unset / empty |
| `PARACHAIN_ID` | Token lookup + labeling | unset (DOT) |
| `REWARD_TOKEN_SYMBOL` / `REWARD_TOKEN_DECIMALS` | Token overrides | unset |
| `FETCH_OPERATOR_NAMES` | Optional name enrichment | `true` |
| `SUBSCAN_API_KEY` | Subscan (Polkadot names) | unset |
| `BEACONCHA_BASE_URL` / `BEACONCHA_API_KEY` | beaconcha.in | `https://beaconcha.in` / unset |
| `RATED_API_KEY` / `RATED_API_BASE_URL` / `RATED_NETWORK` | Rated operator directory | unset / Rated defaults |
| `ENS_LOOKUP_ENABLED` / `ENS_API_KEY` / `ENS_API_BASE_URL` | ENS primary names | `false` / unset / ENSWhois |
| `DEMO_MODE` | Simulated duties | `true` |
| `POLL_INTERVAL_SECONDS` | Cache window | `12` |
| `HOST` / `PORT` | Bind address | `127.0.0.1` / `3000` |
| `ALERT_MISSED_ATTESTATIONS` | Alert if missed primary duties ≥ N | `2` |
| `ALERT_EFFECTIVENESS_BELOW` | Alert if effectiveness &lt; N% | `95` |
| `ALERT_SLASHING_RISK_ABOVE` | Alert if risk score ≥ N | `40` |
| `ALERT_DISK_USAGE_ABOVE` / `ALERT_CLOCK_DRIFT_MS` | Host thresholds | `85` / `500` |

## Alerting

| Channel | Variables |
| --- | --- |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Slack | `SLACK_WEBHOOK_URL` |
| Discord | `DISCORD_WEBHOOK_URL` |
| Webhook | `WEBHOOK_URL` |
| PagerDuty | `PAGERDUTY_ROUTING_KEY` |

```bash
curl -X POST http://127.0.0.1:3000/api/alerts/test
```

## Metrics & API

```text
GET /api/metrics
```

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Dashboard |
| `GET` | `/api/status` | Full health snapshot (JSON) |
| `GET` | `/api/metrics` | Prometheus text exposition |
| `POST` | `/api/collect` | Force a collection cycle |
| `POST` | `/api/alerts/test` | Send a test alert |

## Scoring

- **Effectiveness (0–100):** weighted completion of primary and secondary duties; late primary duties get partial credit.
- **Risk (0–100):** rises with consecutive misses, missed secondary duties, clock drift, syncing, low peers, and low effectiveness. Adapters set `risk_kind` / `risk_label` (slashing, kickout, jail, downtime, …)—confirmed slash/jail/tombstone events use `protocol_events`.

## Tests

```bash
pytest
```

## Support the project

If ValidatorPulse helps you, donations are welcome — ETH or ERC-20 on Ethereum:

```text
0xE5B2f8a35c0f12304c5aBDa9477159b53f622cAA
```
