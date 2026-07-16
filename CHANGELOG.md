# Changelog

All notable changes to DashSnap.

## [0.0.11] - 2026-07-16

### Fixed
- Config save UI no longer shows JSON parse error when addon restarts — ingress 502 on restart is now treated as success ("Saved. Restarting addon…")

## [0.0.10] - 2026-07-16

### Fixed
- Config now survives addon restart — saved to `/data/dashsnap.json` which supervisor never overwrites (supervisor wipes `/data/options.json` on restart when schema is empty)

## [0.0.9] - 2026-07-16

### Fixed
- Config no longer wiped after addon restart — options written to `options.json` before triggering restart, so supervisor wipe on restart has no effect
- Config save no longer returns 502 when addon restarts mid-request — `ServerDisconnectedError` on restart treated as success

## [0.0.8] - 2026-07-16

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

### Changed
- **HA addon Options panel removed** — configure exclusively via the ingress UI (masked tokens, visual editor)
- `options.json` config path for Docker/devcontainer users only

### Fixed
- Config save works correctly after options schema removal — supervisor fallback writes `options.json` directly when supervisor rejects the options POST

---

For earlier versions see the [release history](https://github.com/italo-lombardi/DashSnap/releases).
