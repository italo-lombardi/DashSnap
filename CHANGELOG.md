# Changelog

All notable changes to DashSnap.

## [0.0.5] - 2026-07-15

### Added
- **`public` built-in target** — always present at top of target list, read-only, `strategy: none`. Works out of the box with `/record?url=https://...` — zero configuration needed.
- `GET /record/ha` returns a clear `400` when the selected target has no `base_url` or is not `ha_token` strategy.
- Recording filenames include a URL path slug: `20260715_201234_lovelace_0.png`.

### Changed
- **`base_url` now optional** — only required for `ha_token` strategy. `http_header` and `none` targets need no base URL. Ingress UI hides the field accordingly.
- Auth strategy moved to top of ingress form — drives which fields appear.
- **Output folder renamed `/media/dashsnap` → `/media/DashSnap`** ⚠️ update your volume mount or set `OUT_DIR=/media/dashsnap` to keep the old path.
- `GET /config` always injects `public` target at top of the returned list.
- `docker-compose.yml` volume mount is now `:rw` so the ingress UI can save config.
- `RECORDINGS_PATH` env var in `.env` (gitignored) lets Docker write recordings into a local HA devcontainer media folder.

### Fixed
- Path traversal hardened: `os.path.realpath` + `startswith(safe_root + os.sep)` on all output paths.
- `OUT_DIR` default corrected to `/media/DashSnap`.
- `_check_target_health` skips HTTP check for targets with no `base_url`.

---

For earlier versions see the [release history](https://github.com/italo-lombardi/DashSnap/releases).
