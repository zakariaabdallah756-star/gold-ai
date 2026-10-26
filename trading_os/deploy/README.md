# Deploying Trading OS 24/7 (headless / cloud)

Everything here runs **Trading OS & Journal** unattended on a Linux VPS — no desktop, no browser on the
server — so the Telegram bridge and signal auto-execution keep working around the clock.

All assets live inside `trading_os/deploy/`:

| File | Purpose |
| --- | --- |
| `Dockerfile` | Production container image (non-root, data on a volume, health check). |
| `Dockerfile.dockerignore` | Keeps the build context small (used by BuildKit). |
| `docker-compose.yml` | One-command deploy with a persistent data volume. |
| `trading-os.service` | systemd unit for running directly on the host (no Docker). |
| `launch.sh` | Headless launch script used by systemd and standalone. |

The app exposes **`GET /api/health`** — a tiny unauthenticated probe returning `200` while the process and its
database are up (`503` otherwise). Docker, compose and any external uptime monitor use it.

---

## Option A — Docker Compose (recommended)

From the **repository root**:

```bash
# optional: the domain you reach the app by (IPs & localhost are always allowed)
export TRADING_OS_ALLOWED_HOSTS="trading.example.com"

docker compose -f trading_os/deploy/docker-compose.yml up -d --build
```

- The journal (database, screenshots, backups) is stored in the named volume `trading-os-data`, so it survives
  restarts and image rebuilds.
- The container restarts automatically (`restart: unless-stopped`) and is health-checked every 30 s.
- Update after pulling new code: `docker compose -f trading_os/deploy/docker-compose.yml up -d --build`.

Check it:

```bash
docker compose -f trading_os/deploy/docker-compose.yml ps
curl -fsS http://localhost:8765/api/health
docker compose -f trading_os/deploy/docker-compose.yml logs -f
```

## Option B — Plain Docker

```bash
docker build -f trading_os/deploy/Dockerfile -t trading-os .
docker run -d --name trading-os --restart unless-stopped \
  -p 8765:8765 \
  -v trading-os-data:/data \
  -e TRADING_OS_ALLOWED_HOSTS="trading.example.com" \
  trading-os
```

## Option C — systemd (no Docker)

Clone the repository to `/opt/trading-os` and create a virtualenv with the runtime dependencies:

```bash
sudo git clone <your-repo-url> /opt/trading-os
cd /opt/trading-os
python3 -m venv .venv
.venv/bin/pip install -r trading_os/requirements.txt
```

Then install the service:

```bash
sudo useradd --system --home /opt/trading-os --shell /usr/sbin/nologin tradingos
sudo chown -R tradingos:tradingos /opt/trading-os
sudo cp trading_os/deploy/trading-os.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trading-os
systemctl status trading-os
journalctl -u trading-os -f
```

Edit the `Environment=` lines in the unit (or `launch.sh`'s variables) to set the data directory, port and
allowed host names.

---

## Networking & security notes

- **Binding `0.0.0.0` only makes the port reachable.** The app still answers *only* to `localhost`, IP
  addresses and the host names in `TRADING_OS_ALLOWED_HOSTS`, and refuses every other `Host` header (no DNS
  rebinding). Add your domain to `TRADING_OS_ALLOWED_HOSTS` when you use one.
- **Put it behind HTTPS.** Terminate TLS with a reverse proxy (Caddy, nginx, Traefik) on the same host and
  forward to `127.0.0.1:8765`. The app reads `X-Forwarded-*` only from a proxy on the same computer.
- **Set a remote-access password** (Trading Plan → Network & access) so other devices must sign in. `/api/health`
  stays reachable without it, for monitors.
- **Keep the data volume backed up** — it holds your journal, screenshots and encrypted secrets.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `TRADING_OS_DATA_DIR` | `/data` (image) | Where the database, screenshots and backups live. |
| `TRADING_OS_ALLOWED_HOSTS` | *(empty)* | Comma-separated domain names to serve (IPs always allowed). |
| `TRADING_OS_PORT` | `8765` | Port (launch script / systemd). |
| `TRADING_OS_HOST` | `0.0.0.0` | Bind address (launch script / systemd). |
| `TRADING_OS_MAX_UPLOAD_MB` | `10` | Max screenshot upload size. |

## Feeding signals in

With the app running, open **Telegram signals → External signal API**, generate a token, and turn
*Accept incoming signals* on. A bridge or bot then posts to:

```
POST http://<host>:8765/api/v1/signals/incoming
Authorization: Bearer <your-token>
Content-Type: application/json

{"text": "XAUUSD BUY\nEntry 2650\nSL 2645\nTP 2660"}
```

The companion `telegram_bridge.py` (repository root) does exactly this from your own Telegram account. To
auto-execute incoming signals on a bound account, arm auto-execution on the same page.
