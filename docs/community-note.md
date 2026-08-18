# Operator community note

Short post for EthStaker, Solana, or Cosmos validator channels. Paste as-is; edit the first line if a channel asks for a title.

---

**ValidatorPulse — self-hosted monitor for Ethereum, Polkadot, Cosmos, Solana, and more**

I open-sourced a small FastAPI box that answers “is my validator operating correctly?” with a live dashboard, Prometheus metrics, and alerts (Telegram, Discord, Slack, email, webhook).

It watches operator duties, consensus health, and host infra so you catch downtime and penalties before they escalate. Demo mode works without a node.

- Landing: https://ehsanhajian.github.io/ValidatorPulse/
- Repo / README: https://github.com/ehsanhajian/ValidatorPulse
- Install: `pip install validator-pulse`, then `validator-pulse init` (writes `.env.local` in the current directory) and `validator-pulse`. Docs: https://github.com/ehsanhajian/ValidatorPulse#installation

One chain per process.

Not a SaaS and not a security scanner — self-hosted only.

---

Do not blast this across every channel. One relevant operator thread is enough.
