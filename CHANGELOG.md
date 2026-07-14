# Changelog

All notable changes to DashSnap.

## [0.0.1] - 2026-07-14

Initial release — generalized from [HA Dashboard Recorder](https://github.com/ilombardi/ha-dashboard-recorder).

### Features

- **Record any web page to `.webm`** or **screenshot to `.png`** via headless Chromium (Playwright).
- **Three auth strategies:**
  - `ha_token` — Home Assistant long-lived token, injected into `localStorage` + `IndexedDB` before page load. Default when a `token` field is present.
  - `http_header` — inject arbitrary HTTP headers (e.g. `Authorization: Bearer …`) on every request. Covers Grafana, Kibana, and any API-key-authenticated app.
  - `none` — no auth. For public pages, LAN-only dashboards, or proxy-authenticated apps.
- **HTTP API on port 8099:**
  - `GET/POST /record?url=…` — record absolute URL (any site).
  - `GET/POST /record?path=/lovelace/0` — record path relative to `base_url` (HA shorthand).
  - `GET /health` — connectivity check (HA API probe for `ha_token`, HTTP HEAD otherwise).
  - `GET /ha/dashboards` — list HA dashboards (ha_token only).
  - `GET/POST /ha/record-all` — record every discovered HA dashboard (ha_token only).
- **Env var config** — run Docker with no config file: `DASHSNAP_BASE_URL`, `DASHSNAP_AUTH_STRATEGY`, `DASHSNAP_AUTH_TOKEN`, `DASHSNAP_AUTH_HEADERS`.
- **Backward-compatible** with HA Dashboard Recorder `options.json` (flat `base_url` + `token` auto-detected).
- **301 redirects** from old paths (`/dashboards` → `/ha/dashboards`, `/record-all` → `/ha/record-all`).
- HA add-on packaging (`config.yaml`, `build.yaml`) for aarch64 + amd64.
- Docker Compose for standalone use.
