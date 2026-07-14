# DashSnap

Record or screenshot any web page via headless Chromium — Home Assistant dashboards, Grafana, public pages, and more.

[![Validate](https://github.com/italo-lombardi/DashSnap/actions/workflows/validate.yml/badge.svg)](https://github.com/italo-lombardi/DashSnap/actions/workflows/validate.yml)

---

## What it does

DashSnap runs a small HTTP API server (port 8099). You call it with a URL or HA path and it records a `.webm` video or takes a `.png` screenshot using a headless Chromium browser. It handles authentication automatically — HA token injection, HTTP header auth (Grafana, Kibana), or no auth for public pages.

---

## Installation

### As a Home Assistant App (HAOS / Supervised)

1. In HA, go to **Settings → Add-ons → Add-on store → ⋮ → Repositories**
2. Add: `https://github.com/italo-lombardi/DashSnap`
3. Install **DashSnap** from the list
4. Configure (see below) and start

### With Docker Compose (standalone)

```bash
git clone https://github.com/italo-lombardi/DashSnap
cd DashSnap
cp options.sample.ha.json options.json   # edit with your settings
docker compose up --build -d
curl http://localhost:8099/health
```

---

## Configuration

DashSnap supports three modes depending on how you run it.

---

### Mode 1 — Single HA target (HA App UI)

The simplest setup. Just fill in two fields in the HA App config tab:

| Field | Description | Example |
|---|---|---|
| `base_url` | Your HA instance URL | `http://homeassistant.local:8123` |
| `token` | HA long-lived access token | `eyJ...` |

Leave `targets_json` empty.

**How to get a long-lived token:**
1. HA → Profile (bottom left) → **Long-lived access tokens** → **Create token**
2. Copy and paste into the `token` field

---

### Mode 2 — Multiple targets (HA App UI or Docker)

Paste a JSON array into the `targets_json` field. Each target has its own URL and auth strategy.

```json
[
  {
    "name": "ha",
    "base_url": "http://homeassistant.local:8123",
    "auth": {
      "strategy": "ha_token",
      "token": "eyJ..."
    }
  },
  {
    "name": "grafana",
    "base_url": "https://grafana.example.com",
    "auth": {
      "strategy": "http_header",
      "headers": { "Authorization": "Bearer glsa_xxxx" }
    }
  },
  {
    "name": "public",
    "base_url": "https://www.example.com",
    "auth": { "strategy": "none" }
  }
]
```

**Auth strategies:**

| Strategy | Use for | Credential |
|---|---|---|
| `ha_token` | Home Assistant | HA long-lived token |
| `http_header` | Grafana, Kibana, any API-key app | Any HTTP header (e.g. `Authorization`) |
| `none` | Public pages, LAN-only dashboards | None |

---

### Mode 3 — Docker with options.json

Mount an `options.json` file. Same format as Mode 2 above (or the flat Mode 1 format for a single HA target).

```yaml
# docker-compose.yml
volumes:
  - ./options.json:/data/options.json:ro
  - ./recordings:/media/dashsnap
```

Recordings are saved to `./recordings/` on the host.

---

### Mode 4 — Docker with environment variables (no config file)

```bash
docker run -d \
  -p 8099:8099 \
  -v ./recordings:/media/dashsnap \
  -e DASHSNAP_BASE_URL=http://homeassistant.local:8123 \
  -e DASHSNAP_AUTH_STRATEGY=ha_token \
  -e DASHSNAP_AUTH_TOKEN=eyJ... \
  dashsnap
```

---

## API

### `GET/POST /record/ha` — Record an HA page

For Home Assistant pages. Just provide the path — the base URL is taken from the target config automatically.

```bash
# Record a dashboard view as a 30-second video
curl "http://localhost:8099/record/ha?path=/lovelace/0&seconds=30"

# Screenshot a dashboard
curl "http://localhost:8099/record/ha?path=/lovelace/0&format=png"

# Record with a specific target
curl "http://localhost:8099/record/ha?path=/energy&target=ha&format=png"
```

**Parameters:**

| Param | Required | Default | Description |
|---|---|---|---|
| `path` | Yes | — | HA route, e.g. `/lovelace/0`, `/history`, `/energy` |
| `target` | No | first target | Named target from config |
| `seconds` | No | 30 | Video duration (max 3600). Ignored for `png`. |
| `format` | No | `webm` | `webm` or `png` |
| `viewport_width` | No | 1920 | Width in pixels |
| `viewport_height` | No | 1080 | Height in pixels |

---

### `GET/POST /record` — Record any URL

For any web page — Grafana, public sites, intranet dashboards.

```bash
# Record Grafana dashboard as video
curl "http://localhost:8099/record?url=https://grafana.example.com/d/xyz&target=grafana&seconds=15"

# Screenshot a public page
curl "http://localhost:8099/record?url=https://www.example.com&target=public&format=png"
```

**Parameters:** same as `/record/ha` but with `url` (required, absolute) instead of `path`.

---

### `GET /health` — Check connectivity

Returns the health status of all configured targets.

```bash
curl http://localhost:8099/health
```

```json
{
  "ok": true,
  "targets": [
    {"name": "ha", "ok": true, "strategy": "ha_token", "base_url": "...", "ha": "API running."},
    {"name": "grafana", "ok": true, "strategy": "http_header", "base_url": "...", "http_status": 200}
  ]
}
```

---

### `GET /targets` — List configured targets

Returns target names and strategies (no secrets).

```bash
curl http://localhost:8099/targets
```

```json
{"ok": true, "targets": [{"name": "ha", "strategy": "ha_token"}, {"name": "grafana", "strategy": "http_header"}]}
```

---

### `GET /ha/dashboards` — List HA dashboards

Lists all user-created HA dashboards with their paths. Requires an `ha_token` target.

```bash
curl http://localhost:8099/ha/dashboards
```

---

## Using from Home Assistant automations

Install the [DashSnap Integration](https://github.com/italo-lombardi/DashSnap-Integration) to trigger recordings directly from HA scripts and automations.

```yaml
# Record an HA dashboard
service: dashsnap.record_ha
data:
  path: /lovelace/0
  target: ha
  seconds: 30
  format: webm

# Record any URL
service: dashsnap.record
data:
  url: https://grafana.example.com/d/xyz
  target: grafana
  format: png
```

---

## Where recordings are saved

| Setup | Default location | How to change |
|---|---|---|
| HA App | `/media/dashsnap/` (HA Media browser) | Not configurable from the App UI |
| Docker Compose | `./recordings/` next to `docker-compose.yml` | Edit the volume mount (see below) |
| Docker run | `/media/dashsnap/` inside the container | Mount a host path with `-v` |

### Docker Compose — point recordings anywhere

Edit the left side of the volume mount in `docker-compose.yml`:

```yaml
volumes:
  - ~/Downloads:/media/dashsnap          # save to Downloads folder
  - /mnt/nas/recordings:/media/dashsnap  # save to NAS
  - ./recordings:/media/dashsnap         # default — next to compose file
```

### Docker run — save to Downloads

```bash
docker run -d \
  -p 8099:8099 \
  -v ~/Downloads:/media/dashsnap \
  -e DASHSNAP_BASE_URL=http://homeassistant.local:8123 \
  -e DASHSNAP_AUTH_STRATEGY=ha_token \
  -e DASHSNAP_AUTH_TOKEN=eyJ... \
  dashsnap
```

The `/record` and `/record/ha` responses always include the full file path inside the container, so you always know exactly what was saved:

```json
{"ok": true, "file": "/media/dashsnap/lovelace_0_20260714_193914.webm"}
```

---

## Sibling projects

- **[DashSnap Integration](https://github.com/italo-lombardi/DashSnap-Integration)** — HA custom integration to trigger DashSnap from automations and scripts
