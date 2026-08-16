# Security Policy

## Reporting

Open a [GitHub issue](https://github.com/ehsanhajian/ValidatorPulse/issues/new) for security-related bugs or concerns.

ValidatorPulse is a self-hosted monitor and does not custody keys or user accounts. Public issues are fine for this project.

## Scope notes

ValidatorPulse monitors validator / node health. It is **not** a security scanner for external attack surfaces.

## Docker Compose

The app port is not published. Reach the dashboard only through Caddy. For anything beyond loopback, enable `Caddyfile.restrict` with `CADDY_ALLOW_IPS` (fail closed) and set `WEB_AUTH_USERNAME` / `WEB_AUTH_PASSWORD`. Scrape metrics on `127.0.0.1:9091` rather than opening the dashboard to Prometheus.
