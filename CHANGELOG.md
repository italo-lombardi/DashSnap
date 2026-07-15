# Changelog

All notable changes to DashSnap.

## [0.0.5] - 2026-07-15

### Added
- **Default `public` target** — when no targets are configured DashSnap now automatically uses a built-in `public` target (`strategy: none`, no base URL). `GET /record?url=https://...` works with zero configuration.
- `GET /record/ha` returns a clear `400` error when the selected target has no `base_url` (e.g. the `public` target), rather than producing a malformed URL.

### Changed
- **Output folder renamed `/media/dashsnap` → `/media/DashSnap`** ⚠️ breaking for existing Docker volume mounts.

  **Migration:** update your volume mount:
  ```yaml
  # docker-compose.yml — change this line
  - ./recordings:/media/DashSnap   # was: /media/dashsnap
  ```
  Or set `OUT_DIR` to keep the old path: `-e OUT_DIR=/media/dashsnap`.

- `GET /config` now returns the `targets` array serialised as `targets_json` when `targets_json` is empty — fixes blank ingress UI after saving config via the HA Add-on config tab.
- Removed redundant `mkdir -p /media/DashSnap` from Dockerfile (runtime `OUT_DIR.mkdir()` handles it).

### Fixed
- Path traversal hardened: output file paths now resolved via `os.path.realpath` + `os.path.commonpath` containment check (closes CodeQL `py/path-injection` alerts).

## [0.0.4] - 2026-07-15

### Added
- **Ingress config UI** — friendly web panel in the HA sidebar to configure targets without editing raw JSON. Accessible via the DashSnap panel in the HA left nav.
  - Target list with Edit / Delete per row
  - Edit form with auth strategy picker (`ha_token` / `http_header` / `none`) — shows/hides relevant fields
  - Token masked on load; shows "Token saved" badge with Replace button when a token is already stored
  - Live config reload on save — no addon restart needed (local/dev mode); auto-restarts under HA supervisor
  - Button label adapts: "Save" locally, "Save & Restart" under supervisor

### Changed
- `config.yaml` description trimmed — detail moved to README
- `ingress: true` + `ingress_port: 8099` added to `config.yaml`

### Fixed
- `GET /config` masks token as `***` — never leaks the token over the network
- Server-side validation of `targets_json` before forwarding to supervisor
- `INGRESS_PORT` env var respected for port binding
- `esc()` now escapes `>` as well as `<`

### Dependencies (bumps from dependabot PRs #5–#11)
- `pytest` >=7.0 → >=9.1.1
- `pytest-asyncio` >=0.21 → >=1.4.0
- `pytest-cov` >=4.0 → >=7.1.0
- `aiohttp` >=3.10 → >=3.14.1
- `playwright` >=1.47.0 → >=1.61.0

### Notes
- Saving via the ingress UI converts a flat `base_url`/`token` config to `targets_json` format. This is a one-way migration — the flat fields are cleared after the first save. `_load_config()` handles both formats so existing automations and API calls are unaffected.

---

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
