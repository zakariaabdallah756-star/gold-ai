"""Deployment assets (v1.8): the headless/cloud files in ``trading_os/deploy/`` must stay in sync with the app.

These are cheap contract checks (no Docker needed): the files exist, and they reference the same port and health
path the app actually serves, so a port or health-route change can't silently break the container config.
"""

from pathlib import Path

from trading_os.config import DEFAULT_PORT

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
HEALTH_PATH = "/api/health"


def read(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


def test_the_deploy_assets_are_present():
    for name in (
        "Dockerfile",
        "Dockerfile.dockerignore",
        "docker-compose.yml",
        "trading-os.service",
        "launch.sh",
        "README.md",
    ):
        assert (DEPLOY / name).is_file(), name


def test_the_launch_script_is_executable_and_headless():
    import os
    import stat

    launch = DEPLOY / "launch.sh"
    assert os.access(launch, os.X_OK) or stat.S_IMODE(launch.stat().st_mode) & 0o111, "launch.sh must be executable"
    body = launch.read_text(encoding="utf-8")
    assert "trading_os/run.py" in body and "--no-browser" in body


def test_the_container_binds_the_app_port_and_probes_health():
    dockerfile = read("Dockerfile")
    compose = read("docker-compose.yml")
    port = str(DEFAULT_PORT)
    assert port in dockerfile and port in compose
    # Both expose the port and health-check the real endpoint.
    assert HEALTH_PATH in dockerfile and HEALTH_PATH in compose
    assert "--no-browser" in dockerfile and "0.0.0.0" in dockerfile


def test_the_systemd_unit_restarts_and_runs_the_launch_script():
    unit = read("trading-os.service")
    assert "Restart=always" in unit and "launch.sh" in unit and "WantedBy=multi-user.target" in unit


def test_the_health_route_the_assets_reference_is_actually_served(client):
    assert client.get(HEALTH_PATH).status_code == 200
