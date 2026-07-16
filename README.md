# DashSnap

<a href="https://github.com/italo-lombardi/DashSnap/releases"><img src="https://img.shields.io/github/v/release/italo-lombardi/DashSnap" alt="GitHub Release"></a>
<a href="https://github.com/italo-lombardi/DashSnap/blob/main/LICENSE"><img src="https://img.shields.io/github/license/italo-lombardi/DashSnap?logo=gnu&logoColor=white" alt="License"></a>
<img src="https://img.shields.io/badge/coverage-100%25-brightgreen" alt="Test Coverage">
[![Validate](https://github.com/italo-lombardi/DashSnap/actions/workflows/validate.yml/badge.svg)](https://github.com/italo-lombardi/DashSnap/actions/workflows/validate.yml)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/italolombardi)
[![PayPal](https://img.shields.io/badge/PayPal-00457C?style=flat&logo=paypal&logoColor=white)](https://paypal.me/ItaloLombardi)

[![Add Repository to HA](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fitalo-lombardi%2FDashSnap)

Record or screenshot any web page via headless Chromium — Home Assistant dashboards, Grafana, public pages, and more. Available as a **Home Assistant Add-on** (HAOS/Supervised) or as a standalone **Docker container**.

---

## What it does

DashSnap runs a small HTTP API server (port 8099). You call it with a URL or HA path and it records a `.webm` video or takes a `.png` screenshot using a headless Chromium browser. It handles authentication automatically — HA token injection, HTTP header auth (Grafana, Kibana), or no auth for public pages.

A built-in **`public` target** is always available — no configuration needed to record any public URL.

---

## Installation

### As a Home Assistant Add-on (HAOS / Supervised)

1. In HA, go to **Settings → Add-ons → Add-on store → ⋮ → Repositories**
2. Add: `https://github.com/italo-lombardi/DashSnap`
3. Install **DashSnap** from the list
4. Configure and start (see below)

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

### Via the ingress UI (recommended)

Once DashSnap is running — whether as an HA add-on or a standalone Docker container — open `http://<host>:8099` in your browser. You'll find a friendly configuration page to add, edit and delete targets without touching JSON.

![Ingress config UI — target list](assets/00_ingress_ui.png)

- **Auth strategy first** — pick `ha_token`, `http_header`, or `none`. Base URL only appears for `ha_token`.
- **Built-in `public` target** — always present at the top, read-only. Use it with `/record?url=https://...` to capture any public page with zero config.
- Tokens are masked — a "Token saved" badge shows when one is already stored
- Changes apply immediately — no restart needed

![Ingress config UI — edit form](assets/01_ingress_edit.png)

### Via options.json (alternative)

Prefer file-based config? Mount `options.json` and edit directly — useful for CI, devcontainers, or scripted setups.

**PRIORITY RULE:** if `targets_json` is set, `base_url` and `token` are ignored entirely.

#### Single HA target

Set `base_url` and `token`. Leave `targets_json` empty.

**How to get a long-lived token:**
1. HA → Profile (bottom left) → **Long-lived access tokens** → **Create token**
2. Copy and paste into the `token` field

#### Multiple targets

Set `targets_json` to a JSON array of targets:

```json
[
  {
    "name": "ha",
    "base_url": "http://homeassistant.local:8123",
    "auth": { "strategy": "ha_token", "token": "eyJ..." }
  },
  {
    "name": "grafana",
    "auth": { "strategy": "http_header", "headers": { "Authorization": "Bearer glsa_xxxx" } }
  },
  {
    "name": "public",
    "auth": { "strategy": "none" }
  }
]
```

**Auth strategies:**

| Strategy | Use for | `base_url` required |
|---|---|---|
| `ha_token` | Home Assistant | Yes |
| `http_header` | Grafana, Kibana, any API-key app | No |
| `none` | Public pages, LAN-only dashboards | No |

#### Mount

Mount an `options.json` file:

```yaml
# docker-compose.yml
volumes:
  - ./options.json:/data/options.json:rw
  - ./recordings:/media/DashSnap
```

---

## API

### `GET/POST /record/ha` — Record an HA page

```bash
curl "http://localhost:8099/record/ha?path=/lovelace/0&seconds=30"
curl "http://localhost:8099/record/ha?path=/lovelace/0&format=png"
```

| Param | Required | Default | Description |
|---|---|---|---|
| `path` | Yes | — | HA route, e.g. `/lovelace/0`, `/energy` |
| `target` | No | first target | Named `ha_token` target |
| `seconds` | No | 30 | Video duration (max 3600). Ignored for `png`. |
| `format` | No | `webm` | `webm` or `png` |
| `viewport_width` | No | 1920 | Width in pixels |
| `viewport_height` | No | 1080 | Height in pixels |

### `GET/POST /record` — Record any URL

```bash
curl "http://localhost:8099/record?url=https://grafana.example.com/d/xyz&target=grafana&seconds=15"
curl "http://localhost:8099/record?url=https://www.example.com&format=png"
```

Same parameters as `/record/ha` but with `url` (required, absolute) instead of `path`. Works with any target including the built-in `public`.

### `GET /health` — Check connectivity and target health

```bash
curl http://localhost:8099/health
```

Response includes `self_urls` — the addon's own reachable addresses. The HA integration uses this to auto-detect the correct internal URL. If autodetect fails, copy the first entry from `self_urls` into the integration config.

```json
{
  "ok": true,
  "targets": [...],
  "self_urls": ["http://172.30.33.12:8099"]
}
```

### `GET /targets` — List configured targets

```bash
curl http://localhost:8099/targets
```

### `GET /ha/dashboards` — List HA dashboards

Requires an `ha_token` target.

```bash
curl http://localhost:8099/ha/dashboards
```

---

## Where recordings are saved

| Setup | Default location |
|---|---|
| HA Add-on | `/media/DashSnap/` (visible in HA Media browser) |
| Docker Compose | `./recordings/` next to `docker-compose.yml` |

Recordings are named by capture timestamp + URL slug, e.g. `20260715_201234_lovelace_0.png`.

---

## Using from HA automations

Install the [DashSnap Integration](https://github.com/italo-lombardi/DashSnap-Integration) to trigger recordings from HA scripts and automations.

```yaml
service: dashsnap.record_ha
data:
  path: /lovelace/0
  seconds: 30
  format: webm
```

---

## Sibling projects

- **[DashSnap Integration](https://github.com/italo-lombardi/DashSnap-Integration)** — HA custom integration to trigger DashSnap from automations and scripts
