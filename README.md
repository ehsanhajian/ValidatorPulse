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
| `polkadot` | Implemented | Parachain collators (`POLKADOT_ROLE=collator`) and relay validators (`POLKADOT_ROLE=validator` or `CHAIN=polkadot-relay`) |
| `cosmos` | Implemented | Cosmos SDK / CometBFT validators (`COSMOS_PROFILE=cosmoshub` or `celestia`) |
| `solana` | Implemented | Solana validators (vote accounts / identity pubkeys) |
| `near` | Implemented | NEAR validators (account IDs; blocks / chunks / endorsements) |
| `cardano` | Implemented | Stake pools via cardano-tracer Prometheus (leader slots, KES, forging) |
| `tezos` | Implemented | Tezos bakers via Octez protocol RPC (attestations, baking rights, participation) |
| `algorand` | Implemented | Algorand participation nodes via local authenticated algod (partkeys, online status) |
| `bsc` | Planned | [#27](https://github.com/ehsanhajian/ValidatorPulse/issues/27) |
| `aptos` | Implemented | Aptos validators via fullnode REST + stake view (proposals, set membership) |
| `sui` | Implemented | Sui validators via GraphQL + optional local Prometheus (proposals, atRisk, reports) |
| `monad` | Planned | [#30](https://github.com/ehsanhajian/ValidatorPulse/issues/30) |
| `avalanche` | Planned | [#31](https://github.com/ehsanhajian/ValidatorPulse/issues/31) |
| `mina` | Planned | [#32](https://github.com/ehsanhajian/ValidatorPulse/issues/32) |
| `multiversx` | Planned | [#33](https://github.com/ehsanhajian/ValidatorPulse/issues/33) |
| `ton` | Planned | [#34](https://github.com/ehsanhajian/ValidatorPulse/issues/34) |

Shared models were generalized for heterogeneous L1s in [#35](https://github.com/ehsanhajian/ValidatorPulse/issues/35). Packaging (Docker / Caddy) is tracked in [#9](https://github.com/ehsanhajian/ValidatorPulse/issues/9).

```env
CHAIN=ethereum
```

### RPC URLs, HTTPS, and TLS

Beacon and Substrate (and future adapters) share one HTTP(S) client:

- Full URLs with `http://` or `https://`, **any port**, and optional path prefixes
- TLS certificate verification **on** by default for `https://`
- Optional private CA (`RPC_TLS_CA_BUNDLE`) or lab-only skip (`RPC_TLS_INSECURE=true`)
- Connectivity probe distinguishes **TLS** failures from connection refused / timeouts; the message lands in consensus `last_error`

```env
BEACON_API_URL=https://beacon.example.com:8443
SUBSTRATE_RPC_URL=https://rpc.example.com
RPC_TLS_VERIFY=true
# RPC_TLS_CA_BUNDLE=/etc/ssl/certs/private-ca.pem
# RPC_TLS_INSECURE=false
RPC_CONNECT_TIMEOUT_SECONDS=8
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
BEACON_API_URL=https://beacon.example.com:8443
VALIDATOR_INDICES=123456,789012
```

Local plain HTTP still works: `BEACON_API_URL=http://127.0.0.1:5052`.

Live mode tracks attestation and proposal duties across polls. Rolling rewards use signed Beacon consensus rewards (attestations, proposals, sync committee)—never `balance − effective_balance`. The UI marks the window **partial** while warming up. Execution tips and MEV are excluded. Demo mode simulates a full window without a beacon node.

**Display names** (fail-soft, cached ~1h): beaconcha.in → Rated operator/pool mapping (`RATED_API_KEY`) → ENS on the withdrawal address (`ENS_LOOKUP_ENABLED=true`) → recent proposal graffiti → index/pubkey fallback. Set `FETCH_OPERATOR_NAMES=false` to disable lookups.

### Polkadot

Polkadot supports two operator roles under `CHAIN=polkadot` (or `CHAIN=polkadot-relay` for validators):

| Role | Env | Node | Identifiers |
| --- | --- | --- | --- |
| Parachain **collator** | `POLKADOT_ROLE=collator` (default) | Parachain / collator RPC | `COLLATOR_ADDRESSES` |
| Relay **validator** (NPoS) | `POLKADOT_ROLE=validator` | Relay-chain RPC | `VALIDATOR_STASH_ADDRESSES` |

#### Parachain collators

![Polkadot collator dashboard (demo mode, Astar / ASTR)](docs/images/dashboard-polkadot.png)

| Identifier | Env var | Example |
| --- | --- | --- |
| Substrate HTTP RPC | `SUBSTRATE_RPC_URL` | `http://127.0.0.1:9933` |
| Collator SS58 | `COLLATOR_ADDRESSES` | `5Grw…,5FHn…` |
| Parachain id | `PARACHAIN_ID` | `2006` (Astar → ASTR) |
| Token overrides | `REWARD_TOKEN_SYMBOL` / `REWARD_TOKEN_DECIMALS` | when not in the built-in map |

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

#### Relay validators

Relay validators participate in BABE/GRANDPA and can be slashed. Monitoring uses era points + block production, `risk_kind=slashing`, and alerts for offline / low era points / sync.

```env
CHAIN=polkadot
POLKADOT_ROLE=validator
DEMO_MODE=false
SUBSTRATE_RPC_URL=http://127.0.0.1:9933
VALIDATOR_STASH_ADDRESSES=5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY
```

Equivalent shortcut:

```env
CHAIN=polkadot-relay
DEMO_MODE=false
SUBSTRATE_RPC_URL=http://127.0.0.1:9933
VALIDATOR_STASH_ADDRESSES=5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY
```

`PARACHAIN_ID` is ignored for relay role (DOT unless you override the token). Live mode currently derives a conservative duty window from relay-node sync/peers/reachability until full staking-storage decoding lands; demo mode simulates era points and blocks without a node.

### Cosmos SDK / CometBFT

One reusable `cosmos` adapter covers Cosmos Hub, Celestia, and other SDK chains via **Bech32 profiles** (`COSMOS_PROFILE`).

| Profile | Operator prefix | Token | Default chain id |
| --- | --- | --- | --- |
| `cosmoshub` (default) | `cosmosvaloper…` | ATOM (6) | `cosmoshub-4` |
| `celestia` | `celestiavaloper…` | TIA (6) | `celestia` |

```env
CHAIN=cosmos
COSMOS_PROFILE=cosmoshub
DEMO_MODE=false
COSMOS_REST_URL=http://127.0.0.1:1317
COSMOS_RPC_URL=http://127.0.0.1:26657
COSMOS_VALIDATOR_OPERATOR_ADDRESSES=cosmosvaloper1...
COSMOS_CHAIN_ID=cosmoshub-4
```

Celestia example:

```env
CHAIN=cosmos
COSMOS_PROFILE=celestia
DEMO_MODE=false
COSMOS_REST_URL=http://127.0.0.1:1317
COSMOS_RPC_URL=http://127.0.0.1:26657
COSMOS_VALIDATOR_OPERATOR_ADDRESSES=celestiavaloper1...
```

Live mode uses Cosmos LCD (staking / slashing params / signing info) plus CometBFT `/status` and `/net_info`. Slashing window thresholds come from chain params when LCD is available. Demo mode is offline and profile-aware, including healthy, near-jail, jailed, and tombstoned validators.

**Safety:** never copy a live consensus key/state directory between nodes — that can cause double signing and tombstoning. ValidatorPulse only observes remote APIs; it does not manage keys.

### Solana

Monitor vote accounts via Solana JSON-RPC (`getVoteAccounts`, `getBlockProduction`, `getHealth` / `getEpochInfo`).

```env
CHAIN=solana
DEMO_MODE=false
SOLANA_RPC_URL=http://127.0.0.1:8899
VALIDATOR_VOTE_ACCOUNTS=Vote1111...,Vote2222...
# Optional when vote accounts are unknown — resolve by validator identity:
# SOLANA_IDENTITY_PUBKEYS=Node1111...
ALERT_SKIP_RATE_ABOVE=10
```

Live mode maps epoch credits → primary duty effectiveness, leader-slot skip rate → secondary duty / risk, and delinquents → critical alerts. Demo mode is offline and includes healthy, high-skip, delinquent, and low-credits validators (SOL / lamports).

### NEAR

Monitor pool / validator account IDs via NEAR JSON-RPC (`status`, `validators`). Optional nearcore Prometheus metrics enrich host diagnosis and soft-fail if unavailable.

```env
CHAIN=near
DEMO_MODE=false
NEAR_RPC_URL=http://127.0.0.1:3030
NEAR_VALIDATOR_ACCOUNT_IDS=pool1.near,pool2.near
# Optional:
# NEAR_METRICS_URL=http://127.0.0.1:3030/metrics
```

Live mode tracks blocks, chunks, and endorsements expected/produced for the current epoch, current/next set membership, prev-epoch kickout reasons, and `is_slashed`. Effectiveness weights blocks highest, then chunks, then endorsements. Kickout risk and malicious slashing use distinct alerts. Epoch counter snapshots reset at epoch boundaries without negative deltas. Demo mode is offline with healthy, near-kickout, set-transition, and slashed validators (NEAR / yoctoNEAR).

### Cardano

Monitor stake pools via **local cardano-tracer** Prometheus metrics from a block producer (node 10.2+ tracing). Cardano has reward loss from missed blocks but **does not slash pool stake** — labels and alerts use operational/reward risk only.

```env
CHAIN=cardano
DEMO_MODE=false
CARDANO_POOL_IDS=pool1...
CARDANO_TRACER_URL=http://127.0.0.1:12789
CARDANO_NODE_NAME=block-producer
CARDANO_NETWORK=mainnet
# Optional (not required for duty monitoring):
# CARDANO_NODE_SOCKET_PATH=/run/cardano/node.socket
ALERT_CARDANO_KES_WARNING=5
ALERT_CARDANO_KES_CRITICAL=1
```

Live mode parses `blocksForged`, `slotsMissed`, leader-slot counters, `remainingKESPeriods`, peers, and epoch/slot from the tracer node page (`/node-slug`). Counter snapshots compute poll-to-poll forged/missed totals without double counting. When tracer metrics are unavailable, duty state is **unknown** (no invented leader slots). Demo mode covers healthy forging, missed slots, KES warning, and KES expired scenarios (ADA / lovelace).

### Tezos

Monitor bakers via **Octez protocol RPC** (REST, not JSON-RPC). Tracks attestation and baking rights, cycle participation (`missed_slots`, `remaining_allowed_missed_slots`), delegate forbidden/deactivated state, and pending denunciations. Optional OpenMetrics at `TEZOS_METRICS_URL` and baker log path for future enrichment — both soft-fail when unavailable.

```env
CHAIN=tezos
DEMO_MODE=false
TEZOS_RPC_URL=http://127.0.0.1:8732
TEZOS_BAKER_ADDRESSES=tz1...,tz2...
# Optional:
# TEZOS_METRICS_URL=http://127.0.0.1:9091/metrics
# TEZOS_BAKER_LOG_PATH=/var/log/tezos/baker.log
ALERT_TEZOS_REMAINING_MISSES_BELOW=2
```

Live mode filters baking/attestation rights per configured delegate, reconciles participation counters, and detects reorgs via head level/hash regression. Forbidden or denounced delegates raise critical slashing alerts (risk 100). Demo mode covers healthy baking, missed rights, low remaining miss budget, and forbidden/double-sign scenarios (XTZ / mutez).

### Algorand

Monitor participation nodes via **local authenticated algod** (`/v2/status`, `/v2/accounts/{address}`, `/v2/participation`). Committee selection is private and probabilistic — the adapter records **observed** votes/proposals only and never invents expected or missed committee duties. Suspension/offline is operational risk (not slashing).

```env
CHAIN=algorand
DEMO_MODE=false
ALGORAND_ALGOD_URL=http://127.0.0.1:8080
# Prefer token file; ALGORAND_ALGOD_TOKEN also works (never logged):
ALGORAND_ALGOD_TOKEN_FILE=/var/lib/algorand/algod.token
ALGORAND_ACCOUNT_ADDRESSES=ABC...,XYZ...
# Optional:
# ALGORAND_METRICS_URL=http://127.0.0.1:9100/metrics
ALERT_ALGORAND_PARTKEY_WARNING_ROUNDS=50000
ALERT_ALGORAND_HEARTBEAT_GAP_ROUNDS=10000
```

Live mode joins account status with participation keys by address, surfaces missing/expired/expiring keys distinctly, and raises critical alerts on Online→Offline or incentive-eligible→false transitions. Algod credentials are redacted from errors and API payloads. Demo mode covers healthy, key-expiring, key-missing, and suspended/offline scenarios (ALGO / microAlgos).

### Aptos

Monitor validators by **staking-pool address** via fullnode REST and Move view functions (`0x1::stake::get_current_epoch_proposal_counts`, set membership, stake). Aptos has reward loss for failed proposals but **no principal slashing** — labels and alerts use reward risk only. Optional Node Inspection metrics enrich diagnosis and soft-fail if unavailable.

```env
CHAIN=aptos
DEMO_MODE=false
APTOS_REST_URL=https://fullnode.mainnet.aptoslabs.com/v1
APTOS_POOL_ADDRESSES=0x...
# Optional:
# APTOS_METRICS_URL=http://127.0.0.1:9101/metrics
# APTOS_API_KEY=
ALERT_APTOS_FAILED_PROPOSALS=3
```

Live mode resolves pool → validator index from `ValidatorSet` (re-checked each epoch), maps successful/failed proposals to duties, and preserves epoch counter snapshots without negative deltas. Demo mode covers active, degraded, and inactive pools (APT / octas).

### Sui

Monitor validators via **Sui GraphQL** (not deprecated JSON-RPC) for epoch/system/validator-set state, plus optional local Prometheus (`9184/metrics`) for proposal/checkpoint duty detail. Safe mode and confirmed **reward slashing** (report records) are critical; **low-stake `atRisk`** stays a distinct signal.

```env
CHAIN=sui
DEMO_MODE=false
SUI_GRAPHQL_URL=https://graphql.mainnet.sui.io/graphql
SUI_VALIDATOR_ADDRESSES=0x...
# Optional local node metrics (soft-fail if unavailable):
# SUI_METRICS_URL=http://127.0.0.1:9184/metrics
ALERT_SUI_AT_RISK_EPOCHS=3
```

Live mode paginates the active validator set, joins configured addresses, and records proposal/checkpoint counter deltas without double counting. Missing local metrics preserves on-chain membership/atRisk/report state and marks duty detail unavailable. Demo mode covers healthy, at-risk, and reward-slashed validators (SUI / MIST).

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

## Web panel authentication

Auth is **optional** (off when unset) so local demos stay frictionless. Set both username and password to protect the dashboard and APIs with HTTP Basic Auth:

```env
WEB_AUTH_USERNAME=<username>
WEB_AUTH_PASSWORD=<password>
```

When enabled, unauthenticated requests to `/`, `/api/status`, `/api/collect`, `/api/alerts/test`, and `/api/metrics` return `401` with a `WWW-Authenticate` challenge (browsers show a login prompt).

For Prometheus, either scrape with Basic auth or set a dedicated metrics token:

```env
WEB_METRICS_TOKEN=scrape-token-here
```

```yaml
# prometheus.yml
scrape_configs:
  - job_name: validatorpulse
    static_configs:
      - targets: ["127.0.0.1:3000"]
    authorization:
      type: Bearer
      credentials: scrape-token-here
    # or: basic_auth: { username: <username>, password: <password> }
```

`WEB_METRICS_TOKEN` is accepted as `Authorization: Bearer …`, `X-Metrics-Token`, or `?token=…` on `/api/metrics` only — it does not unlock the dashboard. If the app binds off loopback without credentials, startup logs a warning.

## Configuration reference

Restart after changing `.env.local` (or use reload via `python -m validator_pulse`).

| Variable | Purpose | Default |
| --- | --- | --- |
| `CHAIN` | Active adapter (`ethereum`, `polkadot`, `polkadot-relay`, `cosmos`, `solana`, `near`, `cardano`, `tezos`, `algorand`, `aptos`, `sui`, …) | `ethereum` |
| `POLKADOT_ROLE` | `collator` or `validator` (ignored when `CHAIN=polkadot-relay`) | `collator` |
| `BEACON_API_URL` | Ethereum consensus HTTP(S) API (any host/port) | unset |
| `VALIDATOR_INDICES` / `VALIDATOR_PUBKEYS` | Ethereum operators | `1,2,3` / empty |
| `SUBSTRATE_RPC_URL` | Polkadot Substrate HTTP(S) RPC (any host/port) | unset |
| `COLLATOR_ADDRESSES` | Parachain collator SS58 list | empty |
| `VALIDATOR_STASH_ADDRESSES` | Relay validator stash SS58 list | empty |
| `COSMOS_PROFILE` | `cosmoshub` or `celestia` Bech32/token profile | `cosmoshub` |
| `COSMOS_REST_URL` / `COSMOS_RPC_URL` | Cosmos LCD + CometBFT RPC | unset |
| `COSMOS_VALIDATOR_OPERATOR_ADDRESSES` | Comma-separated `*valoper…` addresses | empty |
| `COSMOS_CHAIN_ID` / `COSMOS_GRPC_URL` | Optional chain id / gRPC (reserved) | unset |
| `SOLANA_RPC_URL` | Solana JSON-RPC HTTP(S) endpoint | unset |
| `VALIDATOR_VOTE_ACCOUNTS` | Comma-separated Solana vote account pubkeys | empty |
| `SOLANA_IDENTITY_PUBKEYS` | Optional identity pubkeys when vote accounts unset | empty |
| `ALERT_SKIP_RATE_ABOVE` | Solana skip-rate alert threshold (percent) | `10` |
| `NEAR_RPC_URL` | NEAR JSON-RPC HTTP(S) endpoint | unset |
| `NEAR_VALIDATOR_ACCOUNT_IDS` | Comma-separated validator / pool account IDs | empty |
| `NEAR_METRICS_URL` | Optional nearcore Prometheus metrics URL | unset |
| `CARDANO_POOL_IDS` | Comma-separated stake pool IDs (`pool1…`) | empty |
| `CARDANO_TRACER_URL` | cardano-tracer Prometheus base URL | unset |
| `CARDANO_NODE_NAME` | Tracer node slug (`TraceOptionNodeName`) | `block-producer` |
| `CARDANO_NETWORK` | Network label (`mainnet`, `preprod`, …) | `mainnet` |
| `ALERT_CARDANO_KES_WARNING` / `ALERT_CARDANO_KES_CRITICAL` | KES period thresholds | `5` / `1` |
| `TEZOS_RPC_URL` | Octez protocol RPC HTTP(S) base URL | unset |
| `TEZOS_BAKER_ADDRESSES` | Comma-separated baker delegate addresses (`tz1…`) | empty |
| `TEZOS_METRICS_URL` | Optional OpenMetrics URL (soft-fail) | unset |
| `TEZOS_BAKER_LOG_PATH` | Optional baker log path (reserved enrichment) | unset |
| `ALERT_TEZOS_REMAINING_MISSES_BELOW` | Alert when allowed attestation misses ≤ N | `2` |
| `ALGORAND_ALGOD_URL` | Local algod REST base URL | unset |
| `ALGORAND_ALGOD_TOKEN` / `ALGORAND_ALGOD_TOKEN_FILE` | Algod API token (env or file; never logged) | unset |
| `ALGORAND_ACCOUNT_ADDRESSES` | Comma-separated account addresses | empty |
| `ALGORAND_METRICS_URL` | Optional algod Prometheus metrics URL | unset |
| `ALERT_ALGORAND_PARTKEY_WARNING_ROUNDS` | Warn when partkey remaining rounds ≤ N | `50000` |
| `ALERT_ALGORAND_HEARTBEAT_GAP_ROUNDS` | Warn when heartbeat lags head by ≥ N rounds | `10000` |
| `APTOS_REST_URL` | Aptos fullnode REST base (`…/v1`) | unset |
| `APTOS_POOL_ADDRESSES` | Comma-separated staking-pool addresses | empty |
| `APTOS_METRICS_URL` | Optional Node Inspection metrics URL | unset |
| `APTOS_API_KEY` | Optional Aptos Labs / gateway API key | unset |
| `ALERT_APTOS_FAILED_PROPOSALS` | Alert when failed proposals ≥ N this epoch | `3` |
| `SUI_GRAPHQL_URL` | Sui GraphQL HTTP endpoint (not JSON-RPC) | unset |
| `SUI_VALIDATOR_ADDRESSES` | Comma-separated validator addresses | empty |
| `SUI_METRICS_URL` | Optional local Prometheus metrics URL | unset |
| `ALERT_SUI_AT_RISK_EPOCHS` | Critical when low-stake atRisk epochs ≥ N | `3` |
| `RPC_TLS_VERIFY` | Verify TLS certs for `https://` RPC URLs | `true` |
| `RPC_TLS_CA_BUNDLE` | Optional CA file path for private PKI | unset |
| `RPC_TLS_INSECURE` | Disable TLS verify (lab only) | `false` |
| `RPC_CONNECT_TIMEOUT_SECONDS` | RPC connect/read timeout | `8` |
| `PARACHAIN_ID` | Collator token lookup + labeling | unset (DOT) |
| `REWARD_TOKEN_SYMBOL` / `REWARD_TOKEN_DECIMALS` | Token overrides | unset |
| `FETCH_OPERATOR_NAMES` | Optional name enrichment | `true` |
| `SUBSCAN_API_KEY` | Subscan (Polkadot names) | unset |
| `BEACONCHA_BASE_URL` / `BEACONCHA_API_KEY` | beaconcha.in | `https://beaconcha.in` / unset |
| `RATED_API_KEY` / `RATED_API_BASE_URL` / `RATED_NETWORK` | Rated operator directory | unset / Rated defaults |
| `ENS_LOOKUP_ENABLED` / `ENS_API_KEY` / `ENS_API_BASE_URL` | ENS primary names | `false` / unset / ENSWhois |
| `DEMO_MODE` | Simulated duties | `true` |
| `POLL_INTERVAL_SECONDS` | Cache window | `12` |
| `HOST` / `PORT` | Bind address | `127.0.0.1` / `3000` |
| `WEB_AUTH_USERNAME` / `WEB_AUTH_PASSWORD` | HTTP Basic auth for panel + APIs (both required) | unset (open) |
| `WEB_METRICS_TOKEN` | Optional Bearer token for `/api/metrics` scrapes | unset |
| `ALERT_MISSED_ATTESTATIONS` | Alert if missed primary duties ≥ N | `2` |
| `ALERT_EFFECTIVENESS_BELOW` | Alert if effectiveness &lt; N% | `95` |
| `ALERT_SLASHING_RISK_ABOVE` | Alert if risk score ≥ N | `40` |
| `ALERT_LOW_ERA_POINTS_BELOW` | Relay: alert if era points &lt; N | `40` |
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
