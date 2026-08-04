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

## Put your validator here

Edit **`.env.local`** (copy from `.env.example` if needed).

Ethereum validators are identified by **index** and/or **BLS pubkey** — not by an execution wallet address (`0x` + 40 hex).

| What you have | Env var | Example |
| --- | --- | --- |
| Validator index | `VALIDATOR_INDICES` | `123456,789012` |
| Validator pubkey (“validator address”) | `VALIDATOR_PUBKEYS` | `0xabc…` (96 hex chars after `0x`) |

You can set either, or both. Multiple validators = comma-separated list.

```env
# Live monitoring
DEMO_MODE=false
BEACON_API_URL=http://127.0.0.1:5052

# Option A — indices
VALIDATOR_INDICES=123456,789012

# Option B — pubkeys (usual "validator address")
VALIDATOR_PUBKEYS=0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

Where to find these:

- **Index / pubkey:** [beaconcha.in](https://beaconcha.in), your validator client logs, or deposit-data JSON
- **Beacon API URL:** your consensus client HTTP API (Lighthouse/Teku/Nimbus/Prysm), often `http://127.0.0.1:5052`

Restart the app after changing `.env.local` (or rely on reload if already running with `python -m validator_pulse`).

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
| `BEACON_API_URL` | Consensus client HTTP API | unset |
| `VALIDATOR_INDICES` | Comma-separated indices | `1,2,3` (demo) |
| `VALIDATOR_PUBKEYS` | Comma-separated BLS pubkeys | empty |
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
