# ValidatorPulse

Keep blockchain validators healthy and prevent downtime and slashing.

**Core question:** *Is my validator operating correctly?*

Python / FastAPI service with a live dashboard, Prometheus metrics, and multi-channel alerting.

## What it monitors

| Area | Signals |
| --- | --- |
| **Validator** | Attestations, block proposals, missed duties, rewards, effectiveness score, slashing risk |
| **Consensus** | Beacon health, sync status, finality, peers |
| **Infrastructure** | CPU, memory, disk usage & latency, network, clock drift |

**Non-goal:** does **not** scan external security surfaces.

## Chains & plugins

ValidatorPulse uses a **chain adapter** plugin layout. Shared dashboard, alerts, metrics, and host infrastructure monitoring sit above adapters; each chain owns consensus + operator collection.

| `CHAIN` | Status | Issue |
| --- | --- | --- |
| `ethereum` | Implemented | — |
| `polkadot` | Implemented (collators) | [#4](https://github.com/ehsanhajian/ValidatorPulse/issues/4) |
| `cosmos` | Planned | [#5](https://github.com/ehsanhajian/ValidatorPulse/issues/5) |
| `solana` | Planned | [#6](https://github.com/ehsanhajian/ValidatorPulse/issues/6) |

Set the active plugin in `.env.local`:

```env
CHAIN=ethereum
```

### Ethereum validators

![Ethereum dashboard (demo mode)](docs/images/dashboard-ethereum.png)

Edit **`.env.local`** (copy from `.env.example` if needed).

Ethereum validators are identified by **index** and/or **BLS pubkey** — not by an execution wallet address (`0x` + 40 hex).

| What you have | Env var | Example |
| --- | --- | --- |
| Validator index | `VALIDATOR_INDICES` | `123456,789012` |
| Validator pubkey (“validator address”) | `VALIDATOR_PUBKEYS` | `0xabc…` (96 hex chars after `0x`) |

```env
CHAIN=ethereum
DEMO_MODE=false
BEACON_API_URL=http://127.0.0.1:5052
VALIDATOR_INDICES=123456,789012
```

Live mode pulls attestation / proposal duties from the Beacon API and **persists** them across poll cycles so missed/success counts and recent-duty lists update over epochs. Its rolling rewards total uses signed consensus-layer attestation, proposal, and sync-committee rewards from the Beacon rewards APIs—never `balance − effective_balance`. Execution tips and MEV payments are outside this total. Demo mode still simulates a full duty window without a beacon node.

### Polkadot / parachain collators

![Polkadot collator dashboard (demo mode, Astar / ASTR)](docs/images/dashboard-polkadot.png)

Collators use **SS58 addresses** and a Substrate HTTP JSON-RPC endpoint (usually your collator node).

| What you have | Env var | Example |
| --- | --- | --- |
| Substrate RPC | `SUBSTRATE_RPC_URL` | `http://127.0.0.1:9933` |
| Collator SS58 addresses | `COLLATOR_ADDRESSES` | `5Grw…,5FHn…` |
| Parachain id (optional) | `PARACHAIN_ID` | `2006` (Astar → ASTR) |
| Token symbol override | `REWARD_TOKEN_SYMBOL` | `ASTR` |
| Token decimals override | `REWARD_TOKEN_DECIMALS` | `18` |

```env
CHAIN=polkadot
DEMO_MODE=false
SUBSTRATE_RPC_URL=http://127.0.0.1:9933
COLLATOR_ADDRESSES=5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY
PARACHAIN_ID=2006
```

Built-in `PARACHAIN_ID` → token mapping:

| ID | Network | Token |
| --- | --- | --- |
| _(unset)_ | Polkadot (default) | DOT |
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

If your parachain is **not** in that table, set `REWARD_TOKEN_SYMBOL` and `REWARD_TOKEN_DECIMALS`. Otherwise leave them unset.

Demo mode (`DEMO_MODE=true` or unset RPC) simulates collation / block-production duties without a node.

Selecting an unimplemented chain returns a clear configuration error (with a link to the tracking issue).

### Add a chain plugin

1. Create `validator_pulse/chains/<name>/adapter.py` implementing `ChainAdapter`
2. Register it in `validator_pulse/chains/registry.py` via `register_adapter(...)`
3. Add chain-specific settings to `Settings` / `.env.example`
4. Keep infrastructure, alerting, and Prometheus export outside the adapter

```python
class ChainAdapter(Protocol):
    name: str
    display_name: str
    operator_label: str  # "validator", "collator", ...

    def is_demo(self, settings: Settings) -> bool: ...

    async def collect(
        self, settings: Settings, infrastructure: InfrastructureHealth
    ) -> ChainCollection: ...
```

## Configuration tips

Restart the app after changing `.env.local` (or rely on reload if already running with `python -m validator_pulse`).

Where to find Ethereum identifiers:

- **Index / pubkey:** [beaconcha.in](https://beaconcha.in), your validator client logs, or deposit-data JSON
- **Beacon API URL:** consensus client HTTP API (Lighthouse/Teku/Nimbus/Prysm), often `http://127.0.0.1:5052`

Where to find Polkadot collator identifiers:

- **SS58 address:** collator account / session keys page on a parachain explorer
- **Substrate RPC:** collator node HTTP RPC, often `http://127.0.0.1:9933`

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env.local
python -m validator_pulse
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000).

With `DEMO_MODE=true` (default), the app simulates duty data so you can explore the UI without a beacon node.

## Configuration reference

| Variable | Purpose | Default |
| --- | --- | --- |
| `CHAIN` | Active chain plugin (`ethereum`, `polkadot`, …) | `ethereum` |
| `BEACON_API_URL` | Ethereum consensus client HTTP API | unset |
| `VALIDATOR_INDICES` | Comma-separated indices | `1,2,3` (demo) |
| `VALIDATOR_PUBKEYS` | Comma-separated BLS pubkeys | empty |
| `SUBSTRATE_RPC_URL` | Polkadot/parachain Substrate HTTP RPC | unset |
| `COLLATOR_ADDRESSES` | Comma-separated SS58 collator addresses | empty |
| `PARACHAIN_ID` | Parachain id (token lookup + labeling) | unset (DOT) |
| `REWARD_TOKEN_SYMBOL` | Override native token symbol | unset |
| `REWARD_TOKEN_DECIMALS` | Override token decimals | unset |
| `FETCH_OPERATOR_NAMES` | Resolve display names via Subscan / beaconcha.in | `true` |
| `SUBSCAN_API_KEY` | Optional Subscan API key | unset |
| `BEACONCHA_BASE_URL` | Ethereum explorer API base | `https://beaconcha.in` |
| `DEMO_MODE` | Force demo data | `true` |
| `POLL_INTERVAL_SECONDS` | Cache / refresh window | `12` |
| `HOST` / `PORT` | Bind address | `127.0.0.1` / `3000` |
| `ALERT_MISSED_ATTESTATIONS` | Alert if missed ≥ N | `2` |
| `ALERT_EFFECTIVENESS_BELOW` | Alert if effectiveness &lt; N% | `95` |
| `ALERT_SLASHING_RISK_ABOVE` | Alert if risk ≥ N | `40` |
| `ALERT_DISK_USAGE_ABOVE` | Alert if disk % ≥ N | `85` |
| `ALERT_CLOCK_DRIFT_MS` | Alert if drift ≥ N ms | `500` |

## Alerting

Set any subset in `.env.local`:

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

## Metrics

Prometheus scrape:

```text
GET /api/metrics
```

Core series:

- `validator_effectiveness_score`
- `validator_missed_attestations_total`
- `validator_slashing_risk_score`
- `validator_rewards_gwei` (rolling net consensus duty rewards)

Also exports per-validator labels plus consensus/infra gauges.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Dashboard |
| `GET` | `/api/status` | Full health snapshot (JSON) |
| `GET` | `/api/metrics` | Prometheus text exposition |
| `POST` | `/api/collect` | Force a collection cycle |
| `POST` | `/api/alerts/test` | Send a test alert |

## Scoring

- **Effectiveness (0–100):** weighted completion of attestations and proposals; late attestations get partial credit.
- **Slashing risk (0–100):** rises with consecutive misses, missed proposals, clock drift, syncing, low peers, and low effectiveness.

## Tests

```bash
pytest
```

## Support the project

If ValidatorPulse helps you, donations are welcome — ETH or ERC-20 on Ethereum:

```text
0xE5B2f8a35c0f12304c5aBDa9477159b53f622cAA
```
