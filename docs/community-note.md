# Operator community note

Short post for EthStaker, Solana, or Cosmos validator channels. Paste as-is; edit the first line if a channel asks for a title.

---

**ValidatorPulse — self-hosted Solana validator monitor (vote lag, credits, skip rate)**

I open-sourced a small FastAPI box that answers “is my validator operating correctly?” with a live dashboard, Prometheus metrics, and alerts (Telegram, Discord, Slack, webhook).

On Solana it watches last-vote lag vs the tip, epoch-credit pace, leader skip rate, and delinquency — plus host CPU/disk/clock. Demo mode works without a node. Other chains still work if you set CHAIN.

- Landing: https://ehsanhajian.github.io/ValidatorPulse/
- Repo / README: https://github.com/ehsanhajian/ValidatorPulse
- Install: `pip install validator-pulse`, then `validator-pulse init` and set `CHAIN=solana`. Docs: https://github.com/ehsanhajian/ValidatorPulse#solana

One chain per process.

Not a SaaS and not a security scanner — self-hosted only.

---

Do not blast this across every channel. One relevant operator thread is enough.
