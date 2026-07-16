# Changelog

All notable changes to DashSnap.

## [0.0.7] - 2026-07-16

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
