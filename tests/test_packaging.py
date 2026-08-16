from __future__ import annotations

from pathlib import Path

from validator_pulse.web import ROOT

REPO = Path(__file__).resolve().parents[1]


def test_dashboard_assets_ship_with_package() -> None:
    assert (ROOT / "templates" / "dashboard.html").is_file()
    assert (ROOT / "static" / "styles.css").is_file()
    assert (ROOT / "static" / "app.js").is_file()
    assert ROOT == Path(__import__("validator_pulse").__file__).resolve().parent


def test_dockerfile_installs_package_without_root_assets() -> None:
    text = (REPO / "Dockerfile").read_text()
    assert "COPY validator_pulse ./validator_pulse" in text
    assert "COPY templates" not in text
    assert "COPY static" not in text
    assert "COPY pyproject.toml README.md" in text
    assert "!README.md" in (REPO / ".dockerignore").read_text()


def test_pages_landing_covers_acceptance() -> None:
    html = (REPO / "docs" / "index.html").read_text()
    assert "Self-hosted validator monitoring" in html
    assert "Ethereum" in html and "Polkadot" in html and "Cosmos" in html and "Solana" in html
    assert "images/dashboard-ethereum.png" in html
    assert "github.com/ehsanhajian/ValidatorPulse#installation" in html
    assert (REPO / "docs" / "images" / "dashboard-ethereum.png").is_file()
    assert (REPO / "docs" / ".nojekyll").exists()


def test_pyproject_has_discoverability_metadata() -> None:
    text = (REPO / "pyproject.toml").read_text()
    assert 'name = "validator-pulse"' in text
    assert "ehsanhajian.github.io/ValidatorPulse" in text
    assert 'validator-pulse = "validator_pulse.__main__:main"' in text
    assert (REPO / "LICENSE").is_file()
    assert "MIT License" in (REPO / "LICENSE").read_text()
