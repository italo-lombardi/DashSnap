# Changelog

All notable changes to DashSnap.

## [0.2.0] - 2026-07-20

### Changed
- **Base image switched to `debian:bookworm-slim` with system `chromium`.** Debian builds Chromium with `proprietary_codecs=true` / `ffmpeg_branding="Chrome"`, so it ships H264 for both amd64 and arm64. Playwright now drives the system browser via `executable_path` (`CHROMIUM_PATH`, default `/usr/bin/chromium`).

### Fixed
- **Live camera streams now render.** Playwright's bundled Chromium had no H264 codec, so Nest (and any H264 WebRTC) cameras came out blank with `Offer must contain H264/90000`. The Debian chromium negotiates H264 (verified: `canPlayType` → `probably`, WebRTC send/recv list H264).
- **Recording no longer hangs on pages with live streams.** `page.goto` used `wait_until="networkidle"`; a live stream never goes idle, so navigation timed out and the recording was aborted (empty file). Now loads with `domcontentloaded` and waits for network quiet only up to 10s.
- **Delayed recordings now produce the correct length.** The `delay` trim used `-c copy`, but Playwright's VP8 webm has a single keyframe at the start, so a copy-mode cut either emitted the whole clip or an empty file (the 509-byte recordings). The trim now re-encodes (`libvpx`) to cut the exact `delay`→`delay+seconds` window.
- Install `tzdata` so `TZ` resolves and filenames use local time instead of UTC. (Completes the 0.1.2 fix.)

## [0.1.2] - 2026-07-20

### Fixed
- Recording filenames now use local time instead of UTC. The container ran in UTC and Python never applied the `TZ` env HA injects; `time.tzset()` is now called at startup so `datetime.now()` reflects the host timezone. Startup log prints `TZ` and local time for diagnosis.

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
