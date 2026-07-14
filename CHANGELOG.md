# Changelog

All notable changes to DashSnap.

## [0.0.3] - 2026-07-14

### Added
- Structured logging — startup logs (targets, default, port), request success/error logs via `logging` module. Visible in `ha apps logs c1b14015_dashsnap`.

### Fixed
- Path injection CodeQL alerts: reassign sanitised `tag` to a typed local variable so the taint trace terminates cleanly.

---

## [0.0.2] - 2026-07-14

### Added
- **Multi-language add-on translations** — field labels and descriptions in 10 languages (da, de, es, fr, it, nb, nl, pl, pt, sv).
- **`targets_json` config field** — paste a JSON array in the HA App UI to configure multiple targets (HA + Grafana + public pages) without Docker.

### Fixed
- Path injection false positives: added explicit `assert` to prove `tag` is alphanumeric-only.
- CI workflows: added `permissions: contents: read` to all jobs.

---

## [0.0.1] - 2026-07-14

Initial release — generalized from HA Dashboard Recorder.

### Features

- **Record any web page to `.webm`** or **screenshot to `.png`** via headless Chromium (Playwright).
- **Three auth strategies:**
  - `ha_token` — Home Assistant long-lived token, injected into `localStorage` + `IndexedDB` before page load. Default when a `token` field is present.
  - `http_header` — inject arbitrary HTTP headers (e.g. `Authorization: Bearer …`) on every request. Covers Grafana, Kibana, and any API-key-authenticated app.
  - `none` — no auth. For public pages, LAN-only dashboards, or proxy-authenticated apps.
- **Named targets** — `options.json` supports multiple targets, each with its own `base_url` and auth config.
- **HTTP API on port 8099:**
  - `GET/POST /record?url=…&target=…` — record any absolute URL.
  - `GET/POST /record/ha?path=…&target=…` — record an HA page by path; `base_url` applied automatically from the named target.
  - `GET /health` — health check for all configured targets (parallel).
  - `GET /targets` — list configured target names and strategies (no secrets).
  - `GET /ha/dashboards` — list HA dashboards (requires an `ha_token` target).
- **Env var config** — run Docker with no config file: `DASHSNAP_BASE_URL`, `DASHSNAP_AUTH_STRATEGY`, `DASHSNAP_AUTH_TOKEN`, `DASHSNAP_AUTH_HEADERS`.
- **Backward-compatible** with old flat `{base_url, token}` config — auto-wrapped as a single `ha_token` target.
- HA add-on packaging (`config.yaml`, `build.yaml`) for aarch64 + amd64.
- Docker Compose for standalone use.
