# ValidatorPulse

**Is my validator operating correctly?**

ValidatorPulse is a FastAPI service that answers that question with a live dashboard, Prometheus metrics, and multi-channel alerts. It watches operator duties, consensus health, and host infrastructure—so you catch downtime and penalties before they escalate.

**Non-goal:** it does not scan external security surfaces.

## Installation

Two install paths. Option A is the lightweight / dev default. Option B puts the dashboard behind Caddy.

### Option A — Python venv (no Docker)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env.local
python -m validator_pulse
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000). With `DEMO_MODE=true` (default), the app simulates duty data so you can explore the UI without a node.

### Option B — Docker Compose + Caddy

```bash
cp compose.env.example .env
docker compose up --build
```

Open [http://127.0.0.1](http://127.0.0.1). Demo mode is the default. The app binds on the Compose network only (`validator-pulse:3000`); Caddy is the only published entrypoint.

| Listener | Default bind | Purpose |
| --- | --- | --- |
| HTTP | `127.0.0.1:80` | Dashboard + `/api/*` |
| HTTPS | `127.0.0.1:443` | Used when `CADDY_SITE_ADDRESS` is a hostname (automatic TLS) |
| Metrics | `127.0.0.1:9091` | `/api/metrics` only, for a local Prometheus scrape |

Copy `.env.example` into `.env` / `.env.local` for chain, RPC, and alert settings. From the container, a node on the host is `host.docker.internal`, not `127.0.0.1`:

```env
BEACON_API_URL=http://host.docker.internal:5052
```

**IP allowlist (fail closed).** Lab Compose uses `deploy/caddy/Caddyfile` (no IP filter). For production:

```env
CADDYFILE=Caddyfile.restrict
CADDY_HTTP_BIND=0.0.0.0
CADDY_HTTPS_BIND=0.0.0.0
CADDY_SITE_ADDRESS=pulse.example.com
CADDY_ALLOW_IPS=203.0.113.10 10.8.0.0/24
```

`Caddyfile.restrict` denies every client unless `CADDY_ALLOW_IPS` lists them (space-separated IPs/CIDRs). An empty list denies all. Set web auth as well if Caddy is reachable beyond this machine.

**Metrics scrape.** Keep scrapes on the loopback listener (`127.0.0.1:9091`) so Prometheus does not need a public dashboard IP. To scrape the public site instead, add the scraper CIDR to `CADDY_ALLOW_IPS`. `WEB_METRICS_TOKEN` still applies on both paths.

```yaml
# prometheus.yml (Compose)
scrape_configs:
  - job_name: validatorpulse
    static_configs:
      - targets: ["127.0.0.1:9091"]
    authorization:
      type: Bearer
      credentials: scrape-token-here
```

Non-goal: Kubernetes / cloud-marketplace packaging. This Compose file is for a single-host operator.

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
| `bsc` | Implemented | BNB Smart Chain validators via SlashIndicator + StakeHub (turns, jail, slash) |
| `aptos` | Implemented | Aptos validators via fullnode REST + stake view (proposals, set membership) |
| `sui` | Implemented | Sui validators via GraphQL + optional local Prometheus (proposals, atRisk, reports) |
| `monad` | Implemented | Monad validators via EVM RPC staking precompile `0x1000` (set, epoch, local duties) |
| `avalanche` | Implemented | Avalanche Primary Network validators via P-Chain + local info/metrics (uptime, runway) |
| `mina` | Implemented | Mina block producers via local GraphQL + CLI/logs (won slots, orphaning, rewards) |
| `multiversx` | Implemented | MultiversX validators via node APIs + gateway heartbeat (rating, jail vs slash) |
| `ton` | Implemented | TON validators via Validation API + QoS catchain efficiency (fines, elections, ADNL history) |

Shared models were generalized for heterogeneous L1s in [#35](https://github.com/ehsanhajian/ValidatorPulse/issues/35). Docker Compose + Caddy packaging is in [#9](https://github.com/ehsanhajian/ValidatorPulse/issues/9).

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

### BNB Smart Chain

Monitor validators by **operator or consensus address** via BSC JSON-RPC system contracts: `SlashIndicator` (`0x1001`) and `StakeHub` (`0x2002`), plus `BSCValidatorSet` (`0x1000`) for the working/living/mining sets. Downtime thresholds are read from `getSlashThresholds()` or explicit `BSC_MISDEMEANOR_THRESHOLD` / `BSC_FELONY_THRESHOLD` — **never hard-coded** (official pages disagree). Double-sign and malicious finality votes are immediately critical. Optional local Geth Prometheus covers node latency.

```env
CHAIN=bsc
DEMO_MODE=false
BSC_RPC_URL=https://bsc-dataseed.binance.org
BSC_VALIDATOR_ADDRESSES=0x...
# Optional:
# BSC_METRICS_URL=http://127.0.0.1:6060/debug/metrics/prometheus
# BSC_SLASH_CONTRACT=0x0000000000000000000000000000000000001001
# BSC_STAKE_HUB_CONTRACT=0x0000000000000000000000000000000000002002
# BSC_MISDEMEANOR_THRESHOLD=
# BSC_FELONY_THRESHOLD=
```

Live mode resolves operator ↔ consensus ↔ vote identity through StakeHub, paginates the hub validator list, tracks working-set membership across Parlia set changes, and surfaces slash-indicator counts, maintenance, jail, and recent slash events. Demo mode covers missed turns, maintenance, double-sign slash, and jail (BNB / wei).

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

### Monad

Monitor validators by **numeric validator ID** via Monad EVM RPC (chain ID **143**) and staking precompile `0x1000` (`getValidator`, paginated `getConsensusValidatorSet`, `getEpoch`, `getProposerValId`). Exact missed-duty claims require local `monad-ledger-tail` JSON or Prometheus; **EVM RPC alone is insufficient**. Automated slashing is not implemented — labels use reward/eligibility risk only.

```env
CHAIN=monad
DEMO_MODE=false
MONAD_RPC_URL=https://rpc.monad.xyz
MONAD_VALIDATOR_IDS=123
# Optional local evidence:
# MONAD_METRICS_URL=http://127.0.0.1:8889/metrics
# MONAD_LEDGER_TAIL_PATH=/var/log/monad/ledger-tail.json
# MONAD_STATUS_PATH=/var/lib/monad/status.json
```

Live mode verifies chain ID 143, paginates the consensus leader set across epochs, and only classifies authored/missed proposals when local ledger or metrics evidence is present. RPC-only mode marks duty history unavailable. Demo mode covers healthy proposals, consensus lag, local failures, and set transitions (MON / wei).

### Avalanche

Monitor **Primary Network** validators by **NodeID** via P-Chain `platform.getCurrentValidators` (membership, stake, start/end, queried-node uptime). Reliable operator uptime requires a **local** node: `info.uptime` exposes two distinct percentages (`rewardingStakePercentage` vs `weightedAveragePercentage`) and only applies when the NodeID is this node. Public `info.uptime` is never treated as the configured validator. Optional `/ext/health` and `/ext/metrics` (polls, connected stake, peers) soft-fail. Custom Avalanche L1s are out of scope. Reward forfeiture is **not** principal slashing.

Uptime requirement follows ACP-267: 80% before Helicon, 90% for periods starting at/after Helicon, or `AVALANCHE_UPTIME_THRESHOLD` (source is always labeled).

```env
CHAIN=avalanche
DEMO_MODE=false
AVALANCHE_RPC_URL=http://127.0.0.1:9650
AVALANCHE_NODE_IDS=NodeID-...
AVALANCHE_NETWORK=mainnet
# Optional:
# AVALANCHE_METRICS_URL=http://127.0.0.1:9650/ext/metrics
# AVALANCHE_UPTIME_THRESHOLD=80
ALERT_AVALANCHE_RUNWAY_HOURS=24
```

Live mode resolves NodeIDs to active Primary Network periods, scores connected stake and poll failures when local metrics exist, and warns before remaining slack cannot recover the threshold. Demo mode covers healthy, near-threshold, and forfeiture cases (AVAX / nAVAX).

### Mina

Monitor **block producers** by `B62…` public key. Correlate locally won private VRF slots (read-only `mina client status` and/or daemon logs) with canonical blocks from local GraphQL `bestChain` / `daemonStatus`. Optional archive JSON or `postgres://` DSN supplies durable history — the GraphQL frontier is only the last ~`k` blocks. Public GraphQL cannot enumerate another producer’s private duties; without local CLI/log evidence, expected duties stay unknown. The adapter is **query-only** (the full GraphQL endpoint can submit transactions). Mina does **not** slash producer stake — labels use reward risk only.

```env
CHAIN=mina
DEMO_MODE=false
MINA_GRAPHQL_URL=http://127.0.0.1:3085/graphql
MINA_PRODUCER_PUBLIC_KEYS=B62q...
MINA_CLIENT_COMMAND=mina
# Optional:
# MINA_ARCHIVE_DATABASE_URL=postgres://...
# MINA_LOG_PATH=/var/log/mina/mina.log
ALERT_MINA_NEAR_SLOT_SLOTS=2
```

Live mode surfaces sync, peers, producer activation, tip height, and freshness lag. Won slots classify as canonical, orphaned, missed, or pending. Near-slot unsynced/inactive state is critical. Demo mode covers schedule, orphan, miss, unsynced, and recovery (MINA / nanomina).

### MultiversX

Monitor validators by **192-hex BLS key** via gateway `/node/heartbeatstatus` and `/validator/statistics`, plus local `/node/status`, `/node/p2pstatus`, and `/node/peerinfo` when configured. Each key on a multikey host is scored independently. Jail from low rating (epoch boundary, threshold labeled from `/network/ratings` or docs) is distinct from serious-offence stake slashing. Recently unjailed validators stay **waiting/passive** while recovering.

```env
CHAIN=multiversx
DEMO_MODE=false
MULTIVERSX_NODE_API_URL=http://127.0.0.1:8080
MULTIVERSX_GATEWAY_URL=https://gateway.multiversx.com
MULTIVERSX_VALIDATOR_BLS_KEYS=<192-hex-key>
# MULTIVERSX_SHARD_ID=0
# MULTIVERSX_JAIL_RATING_THRESHOLD=10
ALERT_MULTIVERSX_RATING_BELOW=20
```

Live mode surfaces heartbeat, shard/state, sync, epoch/round, peers, version, and freshness. Proposal/signature counters and rating feed scoring when the gateway or local node exposes them. Demo mode covers healthy, degrading, jailed, and unjail recovery (EGLD / wei).

### TON

Monitor validators by **64-hex ADNL** identity. Public [Validation API](https://elections.toncenter.com/docs) (`/getValidationCycles`, `/getElections`) supplies round membership, stake, index, complaints and election entries. Catchain efficiency comes from TON Center [QoS `cycleScoreboard`](https://toncenter.com/api/qos/index.html) (schema pinned: `efficiency` / `efficiency_mc` / `efficiency_wc` may be **null** — that is not treated as 0%). Optional local **MyTonCtrl** (`status`, `vl`, `cl`, `el`, `check_ef`) is read-only, argument-safe, and never uses a shell. Optional Prometheus scrapes MyTonCtrl gauges (`validator_masterchain_out_of_sync_seconds`, `validator_console_up`, …). ADNL rotation keeps a time-bounded history window. Completed rounds below the labeled 90% policy (docs, overridable) alert; complaints/fines are **fine risk**, not Ethereum-style principal slashing. Zero efficiency at round start is ignored.

```env
CHAIN=ton
DEMO_MODE=false
TON_VALIDATION_API_URL=https://elections.toncenter.com
TON_ADNL_ADDRESSES=<64-hex-adnl>
# TON_QOS_API_URL=https://toncenter.com
# TON_PROMETHEUS_URL=http://127.0.0.1:9091/metrics
# TON_MYTONCTRL_COMMAND=mytonctrl
TON_NETWORK=mainnet
# TON_EFFICIENCY_THRESHOLD=90
ALERT_TON_EFFICIENCY_BELOW=90
```

Live mode surfaces efficiency, sync lag, validator index (masterchain if `< 100`), election state and source freshness. Demo mode covers healthy, degrading, fined, and recovery rounds (TON / nanoton).

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
# prometheus.yml (Option A — venv)
scrape_configs:
  - job_name: validatorpulse
    static_configs:
      - targets: ["127.0.0.1:3000"]
    authorization:
      type: Bearer
      credentials: scrape-token-here
    # or: basic_auth: { username: <username>, password: <password> }
```

`WEB_METRICS_TOKEN` is accepted as `Authorization: Bearer …`, `X-Metrics-Token`, or `?token=…` on `/api/metrics` only — it does not unlock the dashboard. If the app binds off loopback without credentials, startup logs a warning. Compose scrapes `127.0.0.1:9091` (see Option B).

## Configuration reference

Restart after changing `.env.local` (or use reload via `python -m validator_pulse`).

| Variable | Purpose | Default |
| --- | --- | --- |
| `CHAIN` | Active adapter (`ethereum`, `polkadot`, `polkadot-relay`, `cosmos`, `solana`, `near`, `cardano`, `tezos`, `algorand`, `bsc`, `aptos`, `sui`, `monad`, `avalanche`, `mina`, `multiversx`, …) | `ethereum` |
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
| `BSC_RPC_URL` | BSC JSON-RPC HTTP(S) endpoint | unset |
| `BSC_VALIDATOR_ADDRESSES` | Comma-separated operator or consensus addresses | empty |
| `BSC_METRICS_URL` | Optional local Geth Prometheus metrics URL | unset |
| `BSC_SLASH_CONTRACT` | SlashIndicator address | `0x…1001` |
| `BSC_STAKE_HUB_CONTRACT` | StakeHub address | `0x…2002` |
| `BSC_MISDEMEANOR_THRESHOLD` / `BSC_FELONY_THRESHOLD` | Optional overrides; otherwise contract `getSlashThresholds()` | unset |
| `APTOS_REST_URL` | Aptos fullnode REST base (`…/v1`) | unset |
| `APTOS_POOL_ADDRESSES` | Comma-separated staking-pool addresses | empty |
| `APTOS_METRICS_URL` | Optional Node Inspection metrics URL | unset |
| `APTOS_API_KEY` | Optional Aptos Labs / gateway API key | unset |
| `ALERT_APTOS_FAILED_PROPOSALS` | Alert when failed proposals ≥ N this epoch | `3` |
| `SUI_GRAPHQL_URL` | Sui GraphQL HTTP endpoint (not JSON-RPC) | unset |
| `SUI_VALIDATOR_ADDRESSES` | Comma-separated validator addresses | empty |
| `SUI_METRICS_URL` | Optional local Prometheus metrics URL | unset |
| `ALERT_SUI_AT_RISK_EPOCHS` | Critical when low-stake atRisk epochs ≥ N | `3` |
| `MONAD_RPC_URL` | Monad EVM JSON-RPC HTTP(S) endpoint | unset |
| `MONAD_VALIDATOR_IDS` | Comma-separated numeric validator IDs | empty |
| `MONAD_METRICS_URL` | Optional local Prometheus/OTel metrics URL | unset |
| `MONAD_LEDGER_TAIL_PATH` | Optional read-only `monad-ledger-tail` JSON/NDJSON | unset |
| `MONAD_STATUS_PATH` | Optional read-only `monad-status` JSON | unset |
| `AVALANCHE_RPC_URL` | Avalanche node HTTP(S) base or P-Chain URL | unset |
| `AVALANCHE_NODE_IDS` | Comma-separated `NodeID-…` validators | empty |
| `AVALANCHE_NETWORK` | `mainnet` or `fuji` | `mainnet` |
| `AVALANCHE_METRICS_URL` | Optional `/ext/metrics` override | derived from RPC origin |
| `AVALANCHE_UPTIME_THRESHOLD` | Optional uptime % override; else ACP-267 Helicon 80/90 | unset |
| `ALERT_AVALANCHE_RUNWAY_HOURS` | Warn when remaining recovery slack < N hours | `24` |
| `MINA_GRAPHQL_URL` | Local Mina daemon GraphQL HTTP endpoint | unset |
| `MINA_PRODUCER_PUBLIC_KEYS` | Comma-separated `B62…` block-producer keys | empty |
| `MINA_CLIENT_COMMAND` | Read-only `mina client status` binary | `mina` |
| `MINA_ARCHIVE_DATABASE_URL` | Optional archive JSON path or `postgres://` DSN | unset |
| `MINA_LOG_PATH` | Optional daemon log path (won slots / produced blocks) | unset |
| `ALERT_MINA_NEAR_SLOT_SLOTS` | Critical when unsynced within N slots of a win | `2` |
| `MULTIVERSX_NODE_API_URL` | Local observer `/node/*` HTTP base | unset |
| `MULTIVERSX_GATEWAY_URL` | Gateway HTTP base (heartbeat, statistics, network) | unset |
| `MULTIVERSX_VALIDATOR_BLS_KEYS` | Comma-separated 192-hex BLS public keys | empty |
| `MULTIVERSX_SHARD_ID` | Optional shard for `/network/status` (`4294967295` = metachain) | unset |
| `MULTIVERSX_JAIL_RATING_THRESHOLD` | Optional jail rating override; else network ratings / docs 10 | unset |
| `ALERT_MULTIVERSX_RATING_BELOW` | Critical when rating ≤ N (near jail) | `20` |
| `TON_VALIDATION_API_URL` | TON Validation API base (`/getValidationCycles`, `/getElections`) | unset |
| `TON_ADNL_ADDRESSES` | Comma-separated 64-hex ADNL identities | empty |
| `TON_QOS_API_URL` | Optional QoS base (`/api/qos/cycleScoreboard`); derived from Toncenter elections host | unset |
| `TON_PROMETHEUS_URL` | Optional MyTonCtrl/Pushgateway scrape URL | unset |
| `TON_MYTONCTRL_COMMAND` | Local read-only MyTonCtrl binary | `mytonctrl` |
| `TON_NETWORK` | `mainnet` or `testnet` (label only) | `mainnet` |
| `TON_EFFICIENCY_THRESHOLD` | Optional completed-round efficiency override; else docs 90% | unset |
| `ALERT_TON_EFFICIENCY_BELOW` | Alert wording companion for the 90% policy | `90` |
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
| `HOST` / `PORT` | Bind address (Compose forces `0.0.0.0:3000` on the internal network) | `127.0.0.1` / `3000` |
| `WEB_AUTH_USERNAME` / `WEB_AUTH_PASSWORD` | HTTP Basic auth for panel + APIs (both required) | unset (open) |
| `WEB_METRICS_TOKEN` | Optional Bearer token for `/api/metrics` scrapes | unset |
| `CADDYFILE` | Compose: `Caddyfile` (lab) or `Caddyfile.restrict` (fail-closed allowlist) | `Caddyfile` |
| `CADDY_SITE_ADDRESS` | Caddy site address (`http://:80` or a hostname for automatic HTTPS) | `http://:80` |
| `CADDY_ALLOW_IPS` | Space-separated IPs/CIDRs; empty + restrict file denies all | unset (deny all when restricted) |
| `CADDY_HTTP_BIND` / `CADDY_HTTPS_BIND` | Host bind for published Caddy ports | `127.0.0.1` |
| `CADDY_HTTP_PORT` / `CADDY_HTTPS_PORT` / `CADDY_METRICS_PORT` | Published Caddy ports | `80` / `443` / `9091` |
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
# Option A
curl -X POST http://127.0.0.1:3000/api/alerts/test
# Option B (through Caddy)
curl -X POST http://127.0.0.1/api/alerts/test
```

## Metrics & API

`GET /api/status` is `schema_version: 2` (`operator_id`, `*_base_units`, `duties`, `risk_score`). Prometheus emits both `operator_*` (preferred; labeled with `chain` / `operator_id`) and legacy `validator_*` so existing scrapes keep working. `*_gwei` is a historical alias for the same integer as `*_base_units`.

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
