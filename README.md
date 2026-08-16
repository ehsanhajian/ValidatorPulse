# ValidatorPulse

**Is my validator operating correctly?**

Self-hosted validator monitoring for Ethereum, Polkadot, Cosmos, Solana, and more — Prometheus metrics and multi-channel alerts. A FastAPI dashboard watches operator duties, consensus health, and host infrastructure so you catch downtime and penalties early.

It does **not** scan external security surfaces. One `CHAIN` per process.

| | |
| --- | --- |
| Landing | [ehsanhajian.github.io/ValidatorPulse](https://ehsanhajian.github.io/ValidatorPulse/) |
| Install | Clone this repo, then Option A (venv) or Option B (Compose) below |
| License | [MIT](LICENSE) |

## Contents

- [Installation](#installation)
- [What it monitors](#what-it-monitors)
- [Chains](#chains)
- [RPC and TLS](#rpc-and-tls)
- [Per-chain setup](#per-chain-setup)
- [Auth and Prometheus](#auth-and-prometheus)
- [Configuration](#configuration)
- [Alerts](#alerts)
- [API](#api)
- [Scoring](#scoring)
- [Tests](#tests)
- [License](#license)

## Installation

`pip install validator-pulse` is **not available yet** (PyPI publisher pending). Install from this git repo.

| Path | You need | Dashboard |
| --- | --- | --- |
| **A — Python venv** | git + Python 3.11+ | [http://127.0.0.1:3000](http://127.0.0.1:3000) |
| **B — Docker Compose** | git + Docker Compose | [http://127.0.0.1](http://127.0.0.1) (port **80**, not 3000) |

Both paths start in demo mode so you can confirm the UI without a node. One `CHAIN` per process — `ethereum,solana` is invalid; run a second instance for a second network.

### Option A — Python venv

1. Clone and enter the repo:

```bash
git clone https://github.com/ehsanhajian/ValidatorPulse.git
cd ValidatorPulse
python3 --version    # must print 3.11 or newer
```

2. Create a virtualenv and install the package **from this checkout** (`pip install -e .` is required; `requirements.txt` alone is not enough):

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
pip install -e .
```

3. Copy the sample env. The app reads `.env.local`, then `.env`:

```bash
cp .env.example .env.local
```

Leave `DEMO_MODE=true` and `CHAIN=ethereum` for the first run.

4. Start the server from the repo root, with the venv still active:

```bash
python -m validator_pulse
```

5. Open [http://127.0.0.1:3000](http://127.0.0.1:3000). You should see the Ethereum demo dashboard.

**Go live (same venv):** edit `.env.local`, then Ctrl+C and start again.

| Step | What to set |
| --- | --- |
| 1 | `CHAIN` to one value from the [Chains](#chains) table |
| 2 | `DEMO_MODE=false` |
| 3 | That chain’s RPC URL and identifiers (copy the matching block from [`.env.example`](.env.example) or [Per-chain setup](#per-chain-setup)) |

```env
CHAIN=ethereum
DEMO_MODE=false
BEACON_API_URL=http://127.0.0.1:5052
VALIDATOR_INDICES=123456
```

### Option B — Docker Compose + Caddy

The app port stays on the Compose network. Caddy is the only published entrypoint.

1. Install [Docker](https://docs.docker.com/get-docker/) with the Compose plugin, then:

```bash
git clone https://github.com/ehsanhajian/ValidatorPulse.git
cd ValidatorPulse
docker compose version
```

2. Copy the Compose sample (this file is **`.env`**, not `.env.example`):

```bash
cp compose.env.example .env
docker compose up --build
```

3. Wait until `validator-pulse` is healthy and Caddy has started. Open [http://127.0.0.1](http://127.0.0.1) — port **80**. Port 3000 is not published.

Compose loads `.env` (Caddy + `CHAIN` / `DEMO_MODE`) and optional `.env.local` (full app settings).

**Go live:** copy the full sample, then point RPC at the host node with `host.docker.internal` (not `127.0.0.1` — that is the container itself):

```bash
cp .env.example .env.local
```

```env
# .env and .env.local
CHAIN=ethereum
DEMO_MODE=false
BEACON_API_URL=http://host.docker.internal:5052
VALIDATOR_INDICES=123456
```

```bash
docker compose up --build
```

| Listener | Default bind | Purpose |
| --- | --- | --- |
| HTTP | `127.0.0.1:80` | Dashboard + `/api/*` |
| HTTPS | `127.0.0.1:443` | Automatic TLS when `CADDY_SITE_ADDRESS` is a hostname |
| Metrics | `127.0.0.1:9091` | `/api/metrics` for a local Prometheus scrape |

| Mode | Settings |
| --- | --- |
| Lab (default) | `CADDYFILE=Caddyfile` in `.env` — no IP filter |
| Production | `CADDYFILE=Caddyfile.restrict`, `CADDY_HTTP_BIND=0.0.0.0`, `CADDY_HTTPS_BIND=0.0.0.0`, `CADDY_SITE_ADDRESS=pulse.example.com`, `CADDY_ALLOW_IPS=203.0.113.10 10.8.0.0/24` |

`Caddyfile.restrict` is fail-closed: an empty allowlist denies everyone. Set `WEB_AUTH_USERNAME` / `WEB_AUTH_PASSWORD` as well if Caddy is reachable beyond this machine. Single-host only — not Kubernetes.

### If the install fails

| Symptom | Likely cause |
| --- | --- |
| `python3: command not found` or version &lt; 3.11 | Install Python 3.11+ and retry Option A |
| `No module named validator_pulse` | venv not active, or you skipped `pip install -e .` |
| Browser cannot connect to `:3000` | Option A only. Option B is [http://127.0.0.1](http://127.0.0.1) (port 80) |
| Browser cannot connect to port 80 | Option B: `docker compose up` is not running, or another process owns `:80` |
| `UnsupportedChainError` / HTTP 400 | Typo in `CHAIN`, or comma-separated chains |
| Demo works, live dashboard is empty | `DEMO_MODE` still `true`, or RPC / identifiers missing |
| Compose cannot reach a node on this machine | Use `host.docker.internal`, not `127.0.0.1` |

## What it monitors

| Area | Signals |
| --- | --- |
| **Operator** | Chain-specific duties, missed work, rewards, effectiveness, operational / penalty risk |
| **Consensus** | Reachability, sync distance, finality, peers |
| **Infrastructure** | CPU, memory, disk, network, clock drift |

Adapters supply labels (`risk_label`, duty names, consensus node name). Scoring, alerts, metrics, and the dashboard stay chain-agnostic.

## Chains

| `CHAIN` | Identifiers | RPC / source | Notes |
| --- | --- | --- | --- |
| `ethereum` | `VALIDATOR_INDICES`, `VALIDATOR_PUBKEYS` | `BEACON_API_URL` | Beacon index / BLS pubkey — not an execution wallet |
| `polkadot` | `COLLATOR_ADDRESSES` or `VALIDATOR_STASH_ADDRESSES` | `SUBSTRATE_RPC_URL` | Collator (default) or relay via `POLKADOT_ROLE` / `polkadot-relay` |
| `cosmos` | `COSMOS_VALIDATOR_OPERATOR_ADDRESSES` | `COSMOS_REST_URL`, `COSMOS_RPC_URL` | `COSMOS_PROFILE=cosmoshub` or `celestia` |
| `solana` | `VALIDATOR_VOTE_ACCOUNTS` | `SOLANA_RPC_URL` | Optional `SOLANA_IDENTITY_PUBKEYS` |
| `near` | `NEAR_VALIDATOR_ACCOUNT_IDS` | `NEAR_RPC_URL` | Optional nearcore metrics |
| `cardano` | `CARDANO_POOL_IDS` | `CARDANO_TRACER_URL` | Reward risk only — no pool-stake slash |
| `tezos` | `TEZOS_BAKER_ADDRESSES` | `TEZOS_RPC_URL` | Octez protocol RPC |
| `algorand` | `ALGORAND_ACCOUNT_ADDRESSES` | `ALGORAND_ALGOD_URL` | Observed votes only; no invented committee duties |
| `bsc` | `BSC_VALIDATOR_ADDRESSES` | `BSC_RPC_URL` | Slash thresholds from the contract, not hard-coded |
| `aptos` | `APTOS_POOL_ADDRESSES` | `APTOS_REST_URL` | Reward risk only — no principal slash |
| `sui` | `SUI_VALIDATOR_ADDRESSES` | `SUI_GRAPHQL_URL` | GraphQL, not deprecated JSON-RPC |
| `monad` | `MONAD_VALIDATOR_IDS` | `MONAD_RPC_URL` | Local ledger/metrics required for missed-duty claims |
| `avalanche` | `AVALANCHE_NODE_IDS` | `AVALANCHE_RPC_URL` | Primary Network only; local `info.uptime` |
| `mina` | `MINA_PRODUCER_PUBLIC_KEYS` | `MINA_GRAPHQL_URL` | Query-only; reward risk only |
| `multiversx` | `MULTIVERSX_VALIDATOR_BLS_KEYS` | node + gateway URLs | Jail (rating) vs stake slash are distinct |
| `ton` | `TON_ADNL_ADDRESSES` | Validation API | Null QoS efficiency is not 0% |

```env
CHAIN=ethereum
```

## RPC and TLS

Shared HTTP(S) client for beacon, Substrate, and other adapters.

| Rule | Detail |
| --- | --- |
| URL | Full `http://` or `https://`, any port, optional path |
| TLS | Verified by default on `https://` |
| Private CA | `RPC_TLS_CA_BUNDLE` |
| Lab only | `RPC_TLS_INSECURE=true` |
| Failures | TLS vs connection-refused / timeout land in consensus `last_error` |

| Variable | Default |
| --- | --- |
| `RPC_TLS_VERIFY` | `true` |
| `RPC_TLS_CA_BUNDLE` | unset |
| `RPC_TLS_INSECURE` | `false` |
| `RPC_CONNECT_TIMEOUT_SECONDS` | `8` |

## Per-chain setup

Each block is the live-mode minimum. Demo mode needs only `CHAIN` (and a profile/role where listed). Full variable list: [`.env.example`](.env.example).

### Ethereum

![Ethereum dashboard (demo mode)](docs/images/dashboard-ethereum.png)

| Required | Example |
| --- | --- |
| `CHAIN=ethereum` | |
| `BEACON_API_URL` | `http://127.0.0.1:5052` or `https://beacon.example.com:8443` |
| `VALIDATOR_INDICES` and/or `VALIDATOR_PUBKEYS` | `123456,789012` / `0x` + 96 hex |

| Optional | Purpose |
| --- | --- |
| `FETCH_OPERATOR_NAMES` | Name lookup (default on) |
| `RATED_API_KEY`, `ENS_LOOKUP_ENABLED` | Rated pool map, ENS on withdrawal address |

```env
CHAIN=ethereum
DEMO_MODE=false
BEACON_API_URL=https://beacon.example.com:8443
VALIDATOR_INDICES=123456,789012
```

- Live rewards are signed Beacon consensus rewards — never `balance − effective_balance`. Execution tips / MEV are excluded.
- Display-name order: beaconcha.in → Rated → ENS → graffiti → index/pubkey.

### Polkadot

![Polkadot collator dashboard (demo mode, Astar / ASTR)](docs/images/dashboard-polkadot.png)

| Role | Env | Identifiers |
| --- | --- | --- |
| Parachain collator | `POLKADOT_ROLE=collator` (default) | `COLLATOR_ADDRESSES` |
| Relay validator | `POLKADOT_ROLE=validator` or `CHAIN=polkadot-relay` | `VALIDATOR_STASH_ADDRESSES` |

| Required | Example |
| --- | --- |
| `SUBSTRATE_RPC_URL` | `http://127.0.0.1:9933` |
| `PARACHAIN_ID` (collator) | `2006` → Astar / ASTR |

```env
CHAIN=polkadot
POLKADOT_ROLE=collator
DEMO_MODE=false
SUBSTRATE_RPC_URL=http://127.0.0.1:9933
COLLATOR_ADDRESSES=5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY
PARACHAIN_ID=2006
```

| `PARACHAIN_ID` | Network | Token |
| --- | --- | --- |
| _(unset)_ | Polkadot default | DOT |
| `1000` | Asset Hub | DOT |
| `2000` | Acala | ACA |
| `2004` | Moonbeam | GLMR |
| `2006` | Astar | ASTR |
| `2007` | Shiden | SDN |
| `2023` | Moonriver | MOVR |
| `2030` | Bifrost | BNC |
| `2034` | Hydration | HDX |
| `2035` | Phala | PHA |
| `2046` | Manta | MANTA |

`PARACHAIN_ID` is ignored on relay (DOT unless you override the token). Relay live mode uses era points + block production until full staking-storage decoding lands.

### Cosmos

| Profile | Operator prefix | Token | Default chain id |
| --- | --- | --- | --- |
| `cosmoshub` | `cosmosvaloper…` | ATOM (6) | `cosmoshub-4` |
| `celestia` | `celestiavaloper…` | TIA (6) | `celestia` |

| Required | Example |
| --- | --- |
| `CHAIN=cosmos` | |
| `COSMOS_PROFILE` | `cosmoshub` or `celestia` |
| `COSMOS_REST_URL` / `COSMOS_RPC_URL` | `:1317` LCD, `:26657` CometBFT |
| `COSMOS_VALIDATOR_OPERATOR_ADDRESSES` | `cosmosvaloper1…` |

```env
CHAIN=cosmos
COSMOS_PROFILE=cosmoshub
DEMO_MODE=false
COSMOS_REST_URL=http://127.0.0.1:1317
COSMOS_RPC_URL=http://127.0.0.1:26657
COSMOS_VALIDATOR_OPERATOR_ADDRESSES=cosmosvaloper1...
```

Never copy a live consensus key/state directory between nodes (double-sign / tombstone). ValidatorPulse only reads remote APIs.

### Solana

| Required | Example |
| --- | --- |
| `SOLANA_RPC_URL` | `http://127.0.0.1:8899` |
| `VALIDATOR_VOTE_ACCOUNTS` | vote pubkeys |
| `ALERT_SKIP_RATE_ABOVE` | `10` (percent) |

Optional: `SOLANA_IDENTITY_PUBKEYS` when vote accounts are unknown. Credits → primary duty; skip rate → secondary / risk; delinquents → critical.

### NEAR

| Required | Example |
| --- | --- |
| `NEAR_RPC_URL` | `http://127.0.0.1:3030` |
| `NEAR_VALIDATOR_ACCOUNT_IDS` | `pool1.near,pool2.near` |

Optional: `NEAR_METRICS_URL`. Tracks blocks / chunks / endorsements, set membership, kickout vs slash.

### Cardano

| Required | Example |
| --- | --- |
| `CARDANO_POOL_IDS` | `pool1…` |
| `CARDANO_TRACER_URL` | `http://127.0.0.1:12789` |
| `CARDANO_NODE_NAME` | tracer slug (default `block-producer`) |

| Optional | Default |
| --- | --- |
| `CARDANO_NETWORK` | `mainnet` |
| `ALERT_CARDANO_KES_WARNING` / `ALERT_CARDANO_KES_CRITICAL` | `5` / `1` |

Uses local cardano-tracer Prometheus (node 10.2+). No invented leader slots when the tracer is down. Operational / reward risk only.

### Tezos

| Required | Example |
| --- | --- |
| `TEZOS_RPC_URL` | `http://127.0.0.1:8732` |
| `TEZOS_BAKER_ADDRESSES` | `tz1…,tz2…` |
| `ALERT_TEZOS_REMAINING_MISSES_BELOW` | `2` |

Optional: `TEZOS_METRICS_URL`, `TEZOS_BAKER_LOG_PATH` (soft-fail). Forbidden / denounced delegates are critical slashing alerts.

### Algorand

| Required | Example |
| --- | --- |
| `ALGORAND_ALGOD_URL` | `http://127.0.0.1:8080` |
| `ALGORAND_ALGOD_TOKEN_FILE` or `ALGORAND_ALGOD_TOKEN` | never logged |
| `ALGORAND_ACCOUNT_ADDRESSES` | participation accounts |

| Optional | Default |
| --- | --- |
| `ALERT_ALGORAND_PARTKEY_WARNING_ROUNDS` | `50000` |
| `ALERT_ALGORAND_HEARTBEAT_GAP_ROUNDS` | `10000` |

Committee selection is private — only **observed** votes/proposals are recorded. Offline / suspension is operational risk, not slashing.

### BNB Smart Chain

| Required | Example |
| --- | --- |
| `BSC_RPC_URL` | `https://bsc-dataseed.binance.org` |
| `BSC_VALIDATOR_ADDRESSES` | operator or consensus `0x…` |

Optional: `BSC_METRICS_URL`, contract overrides, `BSC_MISDEMEANOR_THRESHOLD` / `BSC_FELONY_THRESHOLD`. Downtime thresholds come from `getSlashThresholds()` unless you override them. Double-sign and malicious finality votes are immediately critical.

### Aptos

| Required | Example |
| --- | --- |
| `APTOS_REST_URL` | `https://fullnode.mainnet.aptoslabs.com/v1` |
| `APTOS_POOL_ADDRESSES` | staking-pool `0x…` |
| `ALERT_APTOS_FAILED_PROPOSALS` | `3` |

Optional: `APTOS_METRICS_URL`, `APTOS_API_KEY`. Failed proposals cost rewards; there is no principal slashing.

### Sui

| Required | Example |
| --- | --- |
| `SUI_GRAPHQL_URL` | `https://graphql.mainnet.sui.io/graphql` |
| `SUI_VALIDATOR_ADDRESSES` | `0x…` |
| `ALERT_SUI_AT_RISK_EPOCHS` | `3` |

Optional local Prometheus: `SUI_METRICS_URL` (`9184/metrics`). Safe mode and confirmed reward slashing are critical; low-stake `atRisk` stays a separate signal.

### Monad

| Required | Example |
| --- | --- |
| `MONAD_RPC_URL` | `https://rpc.monad.xyz` (chain ID **143**) |
| `MONAD_VALIDATOR_IDS` | numeric IDs |

Optional local evidence: `MONAD_METRICS_URL`, `MONAD_LEDGER_TAIL_PATH`, `MONAD_STATUS_PATH`. EVM RPC alone cannot claim missed duties. Automated slashing is not implemented — reward / eligibility risk only.

### Avalanche

| Required | Example |
| --- | --- |
| `AVALANCHE_RPC_URL` | `http://127.0.0.1:9650` |
| `AVALANCHE_NODE_IDS` | `NodeID-…` |
| `ALERT_AVALANCHE_RUNWAY_HOURS` | `24` |

| Optional | Purpose |
| --- | --- |
| `AVALANCHE_NETWORK` | `mainnet` or `fuji` |
| `AVALANCHE_METRICS_URL` | `/ext/metrics` |
| `AVALANCHE_UPTIME_THRESHOLD` | else ACP-267 (80% pre-Helicon, 90% after) |

Primary Network only. Reliable uptime needs a **local** node (`info.uptime`). Public `info.uptime` is never treated as the configured validator. Reward forfeiture is not principal slashing.

### Mina

| Required | Example |
| --- | --- |
| `MINA_GRAPHQL_URL` | `http://127.0.0.1:3085/graphql` |
| `MINA_PRODUCER_PUBLIC_KEYS` | `B62…` |
| `ALERT_MINA_NEAR_SLOT_SLOTS` | `2` |

Optional: `MINA_CLIENT_COMMAND`, `MINA_ARCHIVE_DATABASE_URL`, `MINA_LOG_PATH`. Public GraphQL cannot list another producer’s private VRF duties. Query-only. No producer-stake slash.

### MultiversX

| Required | Example |
| --- | --- |
| `MULTIVERSX_NODE_API_URL` | `http://127.0.0.1:8080` |
| `MULTIVERSX_GATEWAY_URL` | `https://gateway.multiversx.com` |
| `MULTIVERSX_VALIDATOR_BLS_KEYS` | 192-hex BLS keys |
| `ALERT_MULTIVERSX_RATING_BELOW` | `20` |

Optional: `MULTIVERSX_SHARD_ID`, `MULTIVERSX_JAIL_RATING_THRESHOLD`. Each key on a multikey host is scored independently. Low-rating jail ≠ serious-offence stake slash.

### TON

| Required | Example |
| --- | --- |
| `TON_VALIDATION_API_URL` | `https://elections.toncenter.com` |
| `TON_ADNL_ADDRESSES` | 64-hex ADNL |
| `ALERT_TON_EFFICIENCY_BELOW` | `90` |

| Optional | Purpose |
| --- | --- |
| `TON_QOS_API_URL` | catchain efficiency (`efficiency` may be **null** — not 0%) |
| `TON_PROMETHEUS_URL` / `TON_MYTONCTRL_COMMAND` | local read-only MyTonCtrl |
| `TON_NETWORK` | `mainnet` or `testnet` (label) |
| `TON_EFFICIENCY_THRESHOLD` | override the 90% completed-round policy |

Native token displays as **GRAM** (formerly Toncoin / TON). The network is still TON; `CHAIN=ton` is unchanged. Base unit stays nanoton. Complaints / fines are fine risk, not Ethereum-style principal slashing. Zero efficiency at round start is ignored.

## Auth and Prometheus

Auth is off when unset (local demos stay open). Set **both** username and password for HTTP Basic on the dashboard and APIs.

| Variable | Protects |
| --- | --- |
| `WEB_AUTH_USERNAME` + `WEB_AUTH_PASSWORD` | `/`, `/api/status`, `/api/collect`, `/api/alerts/test`, `/api/metrics` |
| `WEB_METRICS_TOKEN` | `/api/metrics` only (`Authorization: Bearer`, `X-Metrics-Token`, or `?token=`) |

`WEB_METRICS_TOKEN` does not unlock the dashboard. Binding off loopback without credentials logs a warning.

| Install | Prometheus target |
| --- | --- |
| A (venv) | `127.0.0.1:3000` |
| B (Compose) | `127.0.0.1:9091` (keep scrapes on loopback) |

```yaml
scrape_configs:
  - job_name: validatorpulse
    static_configs:
      - targets: ["127.0.0.1:9091"]   # Compose; use :3000 for venv
    authorization:
      type: Bearer
      credentials: scrape-token-here
```

To scrape a public Caddy site instead, add the scraper CIDR to `CADDY_ALLOW_IPS`.

## Configuration

Restart after changing `.env.local` (or rely on reload with `python -m validator_pulse`). Chain-specific variables live in [Per-chain setup](#per-chain-setup). Everything else:

### Process

| Variable | Purpose | Default |
| --- | --- | --- |
| `CHAIN` | Active adapter (one per process) | `ethereum` |
| `DEMO_MODE` | Simulated duties | `true` |
| `POLL_INTERVAL_SECONDS` | Cache window | `12` |
| `HOST` / `PORT` | Bind (Compose forces `0.0.0.0:3000` internally) | `127.0.0.1` / `3000` |
| `REWARD_TOKEN_SYMBOL` / `REWARD_TOKEN_DECIMALS` | Token overrides | unset |

### Lookups

| Variable | Purpose | Default |
| --- | --- | --- |
| `FETCH_OPERATOR_NAMES` | Optional name enrichment | `true` |
| `SUBSCAN_API_KEY` | Polkadot names | unset |
| `BEACONCHA_BASE_URL` / `BEACONCHA_API_KEY` | beaconcha.in | `https://beaconcha.in` / unset |
| `RATED_API_KEY` / `RATED_API_BASE_URL` / `RATED_NETWORK` | Rated operator directory | unset / Rated defaults |
| `ENS_LOOKUP_ENABLED` / `ENS_API_KEY` / `ENS_API_BASE_URL` | ENS primary names | `false` / unset / ENSWhois |

### Caddy (Compose)

| Variable | Purpose | Default |
| --- | --- | --- |
| `CADDYFILE` | `Caddyfile` (lab) or `Caddyfile.restrict` | `Caddyfile` |
| `CADDY_SITE_ADDRESS` | `http://:80` or hostname for HTTPS | `http://:80` |
| `CADDY_ALLOW_IPS` | Space-separated IPs/CIDRs; empty + restrict denies all | unset |
| `CADDY_HTTP_BIND` / `CADDY_HTTPS_BIND` | Published binds | `127.0.0.1` |
| `CADDY_HTTP_PORT` / `CADDY_HTTPS_PORT` / `CADDY_METRICS_PORT` | Published ports | `80` / `443` / `9091` |

### Shared alerts

| Variable | Purpose | Default |
| --- | --- | --- |
| `ALERT_MISSED_ATTESTATIONS` | Missed primary duties ≥ N | `2` |
| `ALERT_EFFECTIVENESS_BELOW` | Effectiveness &lt; N% | `95` |
| `ALERT_SLASHING_RISK_ABOVE` | Risk score ≥ N | `40` |
| `ALERT_LOW_ERA_POINTS_BELOW` | Relay era points &lt; N | `40` |
| `ALERT_DISK_USAGE_ABOVE` / `ALERT_CLOCK_DRIFT_MS` | Host thresholds | `85` / `500` |

## Alerts

| Channel | Variables |
| --- | --- |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Slack | `SLACK_WEBHOOK_URL` |
| Discord | `DISCORD_WEBHOOK_URL` |
| Webhook | `WEBHOOK_URL` |
| PagerDuty | `PAGERDUTY_ROUTING_KEY` |

```bash
# A (venv)
curl -X POST http://127.0.0.1:3000/api/alerts/test
# B (Compose / Caddy)
curl -X POST http://127.0.0.1/api/alerts/test
```

## API

`GET /api/status` is `schema_version: 2` (`operator_id`, `*_base_units`, `duties`, `risk_score`). Prometheus emits `operator_*` (preferred; `chain` / `operator_id` labels) and legacy `validator_*`. `*_gwei` is the same integer as `*_base_units`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Dashboard |
| `GET` | `/api/status` | Full health snapshot (JSON) |
| `GET` | `/api/metrics` | Prometheus text exposition |
| `POST` | `/api/collect` | Force a collection cycle |
| `POST` | `/api/alerts/test` | Send a test alert |

## Scoring

| Score | Range | Meaning |
| --- | --- | --- |
| Effectiveness | 0–100 | Weighted primary / secondary duty completion; late primary duties get partial credit |
| Risk | 0–100 | Consecutive misses, missed secondary duties, clock drift, sync, low peers, low effectiveness |

Adapters set `risk_kind` / `risk_label` (slashing, kickout, jail, downtime, …). Confirmed slash / jail / tombstone events use `protocol_events`.

## Tests

```bash
pytest
```

## License

MIT. See [LICENSE](LICENSE).

## Support the project

If ValidatorPulse helps you, donations are welcome — ETH or ERC-20 on Ethereum:

```text
0xE5B2f8a35c0f12304c5aBDa9477159b53f622cAA
```
