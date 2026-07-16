# Changelog

All notable changes to DashSnap.

## [0.1.1] - 2026-07-16

### Fixed
- `run.sh` now exports `SHADOW_CONFIG_PATH` with a default of `/data/dashsnap.json` — can be overridden via Docker `-e SHADOW_CONFIG_PATH=...` (same pattern as `CONFIG_PATH`)

## [0.1.0] - 2026-07-16

### Added
- Record any web page as `.webm` video or `.png` screenshot via headless Chromium
- `/record/ha` — record an HA dashboard by path with automatic token injection
- `/record` — record any URL with configurable auth target
- `/health` — target connectivity check; includes `self_urls` for HA integration autodetect
- `/targets` — list configured targets
- `/ha/dashboards` — list HA Lovelace dashboards (requires `ha_token` target)
- **`public` built-in target** — always available, zero config, works with `/record?url=https://...`
- **Ingress UI** — visual target editor with masked tokens, available at port 8099 in both HA addon and Docker
- **Three auth strategies**: `ha_token` (HA token injection), `http_header` (Grafana, Kibana, any API-key service), `none` (public/LAN pages)
- Recording filenames include timestamp + URL slug: `20260716_120000_lovelace_0.png`
- Sidebar icon (`mdi:monitor-screenshot`) in HA left menu
- `self_urls` in `/health` response — HA integration uses this to auto-detect the correct internal addon address
- `delay` parameter settles the page before recording begins — video duration equals `seconds` exactly
- Favicon served at `/favicon.ico`
- Config save UI auto-reloads the page 8s after addon restart

### Changed
- **HA addon Options panel removed** — configure exclusively via the ingress UI (masked tokens, visual editor)
- `options.json` config path for Docker/devcontainer users only
- Config persisted to `/data/dashsnap.json` — survives supervisor wipe on addon restart

### Fixed
- Config survives addon restart — saved to `/data/dashsnap.json` which supervisor never overwrites
- Config save no longer returns 502 on restart — `ServerDisconnectedError` treated as success
- Config save UI no longer shows JSON parse error when addon restarts mid-request
