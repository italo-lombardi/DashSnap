import asyncio
import json
import logging
import os
import pathlib
import re
import shutil
from datetime import datetime

import aiohttp
from aiohttp import web
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Config load + env var overlay + backward-compat shim
# ---------------------------------------------------------------------------


def _load_config():
    global CFG, TARGETS, DEFAULT_TARGET
    cfg_path = os.environ.get("CONFIG_PATH", "/data/options.json")
    try:
        with open(cfg_path) as _f:
            cfg = json.load(_f)
    except FileNotFoundError:
        cfg = {}

    if "targets" not in cfg:
        targets_json = cfg.get("targets_json", "").strip()
        if targets_json:
            try:
                cfg["targets"] = json.loads(targets_json)
            except json.JSONDecodeError as e:
                raise SystemExit(f"targets_json is not valid JSON: {e}") from e
        elif "base_url" in cfg:
            if "token" in cfg and "auth" not in cfg:
                cfg["auth"] = {"strategy": "ha_token", "token": cfg["token"]}
            cfg["targets"] = [
                {
                    "name": "default",
                    "base_url": cfg["base_url"],
                    "auth": cfg.get("auth", {"strategy": "ha_token"}),
                }
            ]

    CFG = cfg
    TARGETS = {t["name"]: t for t in CFG.get("targets", [])}
    DEFAULT_TARGET = next(iter(TARGETS)) if TARGETS else None


_load_config()  # pragma: no cover

log = logging.getLogger("dashsnap")

OUT_DIR = pathlib.Path(os.environ.get("OUT_DIR", "/media/dashsnap"))

DEFAULTS = {
    "seconds": 30,
    "viewport_width": 1920,
    "viewport_height": 1080,
}

# ---------------------------------------------------------------------------
# Auth strategies
# ---------------------------------------------------------------------------


async def _auth_none(context, page, auth_cfg, base_url):  # pragma: no cover
    pass


async def _auth_http_header(context, page, auth_cfg, base_url):  # pragma: no cover
    headers = auth_cfg.get("headers", {})
    if headers:
        await context.set_extra_http_headers(headers)


async def _auth_ha_token(context, page, auth_cfg, base_url):  # pragma: no cover
    token = auth_cfg.get("token", "")
    token_blob = json.dumps(
        {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 1800,
            "hassUrl": base_url,
            "clientId": base_url + "/",
            "expires": 9999999999999,
            "refresh_token": "",
        }
    )
    await context.add_init_script(
        """(() => {
          const blob = __TOKEN_BLOB__;
          try { localStorage.setItem('hassTokens', JSON.stringify(blob)); } catch (e) {}
          try {
            const open = indexedDB.open('home-assistant', 1);
            open.onupgradeneeded = (ev) => {
              const db = ev.target.result;
              if (!db.objectStoreNames.contains('tokens')) db.createObjectStore('tokens');
            };
            open.onsuccess = (ev) => {
              const db = ev.target.result;
              try {
                const tx = db.transaction('tokens', 'readwrite');
                tx.objectStore('tokens').put(blob, 'hassTokens');
              } catch (e) {}
            };
          } catch (e) {}
        })();""".replace("__TOKEN_BLOB__", token_blob)
    )
    # Land on origin first, await IndexedDB commit before target nav (eliminates race)
    await page.goto(f"{base_url}/", wait_until="domcontentloaded")
    await page.evaluate(
        """async (blob) => {
            try { localStorage.setItem('hassTokens', JSON.stringify(blob)); } catch (e) {}
            await new Promise((resolve) => {
              let done = false;
              const finish = () => { if (!done) { done = true; resolve(); } };
              try {
                const open = indexedDB.open('home-assistant', 1);
                open.onupgradeneeded = (ev) => {
                  const db = ev.target.result;
                  if (!db.objectStoreNames.contains('tokens')) db.createObjectStore('tokens');
                };
                open.onsuccess = (ev) => {
                  const db = ev.target.result;
                  try {
                    const tx = db.transaction('tokens', 'readwrite');
                    tx.objectStore('tokens').put(blob, 'hassTokens');
                    tx.oncomplete = finish;
                    tx.onerror = finish;
                  } catch (e) { finish(); }
                };
                open.onerror = finish;
              } catch (e) { finish(); }
            });
        }""",
        json.loads(token_blob),
    )


AUTH_STRATEGIES = {
    "none": _auth_none,
    "http_header": _auth_http_header,
    "ha_token": _auth_ha_token,
}

# ---------------------------------------------------------------------------
# Core recorder
# ---------------------------------------------------------------------------


async def record(url, seconds, vw, vh, fmt="webm", target_name=None):  # pragma: no cover
    target = TARGETS.get(target_name or DEFAULT_TARGET)
    if target is None:
        raise ValueError(f"unknown target: {target_name!r}. Configured: {list(TARGETS)}")
    base_url = target["base_url"].rstrip("/")
    auth_cfg = target.get("auth", {"strategy": "none"})
    strategy = auth_cfg.get("strategy", "none")

    apply_auth = AUTH_STRATEGIES.get(strategy)
    if apply_auth is None:
        raise ValueError(f"unknown auth strategy: {strategy!r}")

    is_ha_url = strategy == "ha_token" and url.startswith(base_url)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Sanitise tag to [a-zA-Z0-9_] only — prevents path traversal in filenames
    _raw_tag = re.sub(r"[^a-zA-Z0-9]+", "_", url.split("://")[-1].strip("/")) or "page"
    if not re.fullmatch(r"[a-zA-Z0-9_]+", _raw_tag):
        raise RuntimeError(f"unexpected tag after sanitisation: {_raw_tag!r}")
    tag = _raw_tag  # taint ends at the fullmatch check above
    is_png = fmt == "png"
    tmp_dir = OUT_DIR / (f".tmp_{tag}_{stamp}")
    if not is_png:
        tmp_dir.mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx_kwargs = {"viewport": {"width": vw, "height": vh}}
        if not is_png:
            ctx_kwargs["record_video_dir"] = str(tmp_dir)
            ctx_kwargs["record_video_size"] = {"width": vw, "height": vh}
        ctx = await browser.new_context(**ctx_kwargs)
        page = await ctx.new_page()
        try:
            if is_ha_url or strategy not in ("ha_token", "none"):
                await apply_auth(ctx, page, auth_cfg, base_url)

            await page.goto(url, wait_until="networkidle")

            if is_ha_url:
                try:
                    await page.wait_for_selector("home-assistant", timeout=15000)
                except Exception:  # noqa: BLE001, SIM105
                    pass
                if await page.query_selector(
                    "input[name='username']"
                ) or not await page.query_selector("home-assistant"):
                    raise RuntimeError(
                        "not authenticated after token inject — frontend did not render. "
                        "Token invalid, or HA auth store shape changed."
                    )

            if is_png:
                final = OUT_DIR / f"{tag}_{stamp}.png"
                await page.screenshot(path=str(final))
                return str(final)

            await page.wait_for_timeout(seconds * 1000)
        finally:
            await ctx.close()
            await browser.close()

    webms = list(tmp_dir.glob("*.webm"))
    if not webms:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError("no video produced")
    final = OUT_DIR / f"{tag}_{stamp}.webm"
    webms[0].replace(final)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return str(final)


# ---------------------------------------------------------------------------
# Health helpers
# ---------------------------------------------------------------------------


async def _check_target_health(target):
    name = target["name"]
    base_url = target["base_url"].rstrip("/")
    auth_cfg = target.get("auth", {"strategy": "none"})
    strategy = auth_cfg.get("strategy", "none")
    try:
        async with aiohttp.ClientSession() as s:
            if strategy == "ha_token":
                token = auth_cfg.get("token", "")
                async with s.get(
                    f"{base_url}/api/",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    body = await r.json() if r.content_type == "application/json" else {}
                    ok = r.status == 200
                    result = {"name": name, "ok": ok, "strategy": strategy, "base_url": base_url}
                    if ok:
                        result["ha"] = body.get("message")
                    else:
                        result["hint"] = "bad token" if r.status == 401 else f"HTTP {r.status}"
                    return result
            else:
                async with s.head(base_url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    ok = 200 <= r.status < 400
                    return {
                        "name": name,
                        "ok": ok,
                        "strategy": strategy,
                        "base_url": base_url,
                        "http_status": r.status,
                    }
    except Exception as e:
        return {
            "name": name,
            "ok": False,
            "strategy": strategy,
            "base_url": base_url,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# HA helpers
# ---------------------------------------------------------------------------


def _ha_target():
    """Return the target named 'ha', else first ha_token target, else None."""
    if "ha" in TARGETS:
        return TARGETS["ha"]
    return next(
        (t for t in TARGETS.values() if t.get("auth", {}).get("strategy") == "ha_token"), None
    )


async def list_dashboards():  # pragma: no cover
    target = _ha_target()
    if target is None:
        raise RuntimeError("no ha_token target configured")
    base_url = target["base_url"].rstrip("/")
    token = target.get("auth", {}).get("token", "")
    ws_url = base_url.replace("http", "ws", 1) + "/api/websocket"
    async with aiohttp.ClientSession() as s:  # noqa: SIM117
        async with s.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=10)) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": token})
            auth = await ws.receive_json()
            if auth.get("type") != "auth_ok":
                raise RuntimeError(f"WS auth failed: {auth.get('type')}")
            await ws.send_json({"id": 1, "type": "lovelace/dashboards/list"})
            resp = await ws.receive_json()
            if not resp.get("success", False):
                raise RuntimeError(f"dashboards/list failed: {resp.get('error')}")
            return [
                {"path": "/" + d["url_path"], "title": d.get("title", d["url_path"])}
                for d in resp["result"]
            ]


# ---------------------------------------------------------------------------
# Request param helpers
# ---------------------------------------------------------------------------


def _params(q):
    try:
        fmt = q.get("format", "webm").lower()
        if fmt not in ("webm", "png"):
            fmt = "webm"
        return {
            "seconds": min(int(q.get("seconds", DEFAULTS["seconds"])), 3600),
            "vw": int(q.get("viewport_width", DEFAULTS["viewport_width"])),
            "vh": int(q.get("viewport_height", DEFAULTS["viewport_height"])),
            "fmt": fmt,
            "target_name": q.get("target") or None,
        }
    except (ValueError, TypeError) as e:
        raise web.HTTPBadRequest(reason=f"invalid param: {e}") from e


# ---------------------------------------------------------------------------
# Request handlers
# ---------------------------------------------------------------------------


async def handle_record(request):
    """POST/GET /record?url=<absolute-url>&target=<name> — record any URL."""
    q = request.query
    url = q.get("url")
    if not url:
        return web.json_response(
            {"ok": False, "error": "missing 'url' — e.g. ?url=https://grafana.example.com/d/xyz"},
            status=400,
        )
    if not url.startswith(("http://", "https://")):
        return web.json_response(
            {"ok": False, "error": "url must start with http:// or https://"},
            status=400,
        )
    p = _params(q)
    try:
        out = await record(url, p["seconds"], p["vw"], p["vh"], p["fmt"], p["target_name"])
    except Exception as e:
        log.error("record failed for %s: %s", url, e)
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    log.info("recorded %s → %s", url, out)
    return web.json_response({"ok": True, "file": out})


async def handle_record_ha(request):
    """POST/GET /record/ha?path=<ha-path>&target=<name> — record an HA page by path."""
    q = request.query
    path = q.get("path")
    if not path:
        return web.json_response(
            {"ok": False, "error": "missing 'path' — e.g. ?path=/lovelace/0"},
            status=400,
        )
    p = _params(q)
    target_name = p["target_name"] or DEFAULT_TARGET
    target = TARGETS.get(target_name)
    if not target:
        return web.json_response(
            {"ok": False, "error": f"unknown target: {target_name!r}"}, status=400
        )
    base = target["base_url"].rstrip("/")
    url = base + ("" if path.startswith("/") else "/") + path
    if not url.startswith(("http://", "https://")):
        return web.json_response(
            {"ok": False, "error": "assembled URL must start with http:// or https://"},
            status=400,
        )
    try:
        out = await record(url, p["seconds"], p["vw"], p["vh"], p["fmt"], target_name)
    except Exception as e:
        log.error("record/ha failed for %s: %s", url, e)
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    log.info("recorded %s → %s", url, out)
    return web.json_response({"ok": True, "file": out})


async def handle_health(request):
    if not TARGETS:
        return web.json_response({"ok": False, "error": "no targets configured"}, status=503)
    results = await asyncio.gather(*[_check_target_health(t) for t in TARGETS.values()])
    ok = all(r["ok"] for r in results)
    return web.json_response({"ok": ok, "targets": list(results)}, status=200 if ok else 502)


async def handle_targets(request):
    targets = [
        {"name": t["name"], "strategy": t.get("auth", {}).get("strategy", "none")}
        for t in TARGETS.values()
    ]
    return web.json_response({"ok": True, "targets": targets})


async def handle_ha_dashboards(request):
    if _ha_target() is None:
        return web.json_response(
            {"ok": False, "error": "no ha_token target configured"}, status=404
        )
    try:
        dashboards = await list_dashboards()
    except Exception as e:
        log.error("ha/dashboards failed: %s", e)
        return web.json_response({"ok": False, "error": str(e)}, status=502)
    return web.json_response({"ok": True, "dashboards": dashboards})


# ---------------------------------------------------------------------------
# Config UI (ingress panel)
# ---------------------------------------------------------------------------

_CONFIG_UI = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DashSnap — Configure</title>
<style>
  :root{--ha:#03a9f4;--bg:#0d1117;--card:#161b22;--border:#21262d;--text:#e6edf3;--muted:#6e7681;--err:#f85149;--ok:#3fb950;--ha-dim:rgba(3,169,244,.1);--radius:8px}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);padding:28px 24px 56px}
  h1{font-size:1.2rem;font-weight:700;color:var(--ha);margin-bottom:2px;display:flex;align-items:center;gap:8px}
  .subtitle{font-size:.78rem;color:var(--muted);margin-bottom:28px}
  .section-label{font-size:.68rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px}
  /* target row */
  .trow{display:flex;align-items:center;gap:10px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:13px 16px;margin-bottom:7px;transition:border-color .15s,box-shadow .15s}
  .trow:hover{border-color:#30363d;box-shadow:0 2px 8px rgba(0,0,0,.3)}
  .trow-info{flex:1;min-width:0}
  .trow-name{font-weight:600;font-size:.88rem;margin-bottom:2px}
  .trow-url{font-size:.73rem;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .badge{font-size:.65rem;font-weight:700;border-radius:4px;padding:3px 7px;white-space:nowrap;letter-spacing:.02em}
  .b-ha  {background:rgba(3,169,244,.12);color:#38bdf8;border:1px solid rgba(3,169,244,.2)}
  .b-hdr {background:rgba(168,85,247,.12);color:#c084fc;border:1px solid rgba(168,85,247,.2)}
  .b-none{background:rgba(110,118,129,.12);color:#8b949e;border:1px solid rgba(110,118,129,.2)}
  .trow-btn{background:none;border:1px solid var(--border);color:var(--muted);cursor:pointer;font-size:.73rem;padding:5px 12px;border-radius:5px;transition:.15s;font-weight:500}
  .trow-btn:hover{border-color:var(--ha);color:var(--ha);background:var(--ha-dim)}
  .trow-btn.del:hover{border-color:var(--err);color:var(--err);background:rgba(248,81,73,.08)}
  /* form panel */
  .edit-panel{background:var(--card);border:1px solid var(--ha);border-radius:var(--radius);padding:22px;margin-bottom:16px;box-shadow:0 0 0 3px rgba(3,169,244,.06),0 8px 32px rgba(0,0,0,.4)}
  .panel-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
  .panel-header h2{font-size:.95rem;font-weight:700;color:var(--ha)}
  .panel-close{background:none;border:none;color:var(--muted);cursor:pointer;font-size:1.1rem;line-height:1;padding:0 2px}
  .panel-close:hover{color:var(--text)}
  label{display:block;font-size:.73rem;font-weight:600;color:var(--muted);margin-bottom:5px;margin-top:14px;letter-spacing:.02em}
  label:first-of-type{margin-top:0}
  .label-row{display:flex;align-items:center;gap:6px;margin-top:14px;margin-bottom:5px}
  .label-row label{margin:0}
  input,select,textarea{width:100%;background:#010409;color:var(--text);border:1px solid var(--border);border-radius:6px;padding:9px 12px;font-size:.875rem;font-family:inherit;transition:border-color .15s,box-shadow .15s}
  textarea{font-family:'SF Mono','Fira Code',monospace;resize:vertical;min-height:68px}
  input:focus,select:focus,textarea:focus{outline:none;border-color:var(--ha);box-shadow:0 0 0 3px rgba(3,169,244,.12)}
  select{cursor:pointer}
  .hint{font-size:.71rem;color:var(--muted);margin-top:5px;line-height:1.5}
  .badge-saved{font-size:.65rem;font-weight:700;background:rgba(63,185,80,.12);color:var(--ok);border:1px solid rgba(63,185,80,.2);border-radius:4px;padding:2px 7px}
  .token-saved-state{display:flex;align-items:center;justify-content:space-between;background:rgba(63,185,80,.08);border:1px solid rgba(63,185,80,.2);border-radius:6px;padding:10px 14px;color:var(--ok);font-size:.875rem;font-weight:600}
  .replace-btn{background:none;border:1px solid rgba(63,185,80,.3);color:var(--ok);border-radius:5px;padding:4px 12px;font-size:.75rem;cursor:pointer;font-weight:600;transition:.15s}
  .replace-btn:hover{background:rgba(63,185,80,.12)}
  .form-actions{display:flex;gap:8px;margin-top:20px}
  .btn-primary{flex:1;padding:10px;background:var(--ha);color:#fff;border:none;border-radius:6px;font-size:.875rem;font-weight:700;cursor:pointer;transition:opacity .15s;letter-spacing:.01em}
  .btn-primary:hover{opacity:.85}
  .btn-cancel{padding:10px 18px;background:none;border:1px solid var(--border);color:var(--muted);border-radius:6px;font-size:.875rem;cursor:pointer;transition:.15s;font-weight:500}
  .btn-cancel:hover{border-color:#30363d;color:var(--text)}
  .add-btn{width:100%;padding:10px;border:1px dashed var(--border);border-radius:var(--radius);background:none;color:var(--muted);cursor:pointer;font-size:.83rem;margin-bottom:20px;transition:.15s;font-weight:500}
  .add-btn:hover{border-color:var(--ha);color:var(--ha);background:var(--ha-dim)}
  .save-btn{width:100%;padding:12px;background:var(--ha);color:#fff;border:none;border-radius:var(--radius);font-size:.95rem;font-weight:700;cursor:pointer;transition:opacity .15s;letter-spacing:.02em}
  .save-btn:hover{opacity:.85}
  .save-btn:disabled{opacity:.4;cursor:default}
  .msg{padding:11px 15px;border-radius:6px;margin-top:12px;font-size:.82rem;display:none;line-height:1.5}
  .msg.ok {background:rgba(63,185,80,.1) ;border:1px solid rgba(63,185,80,.25) ;color:var(--ok) ;display:block}
  .msg.err{background:rgba(248,81,73,.1) ;border:1px solid rgba(248,81,73,.25) ;color:var(--err);display:block}
  footer{margin-top:44px;padding-top:16px;border-top:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}
  .footer-left{font-size:.73rem;color:var(--muted);line-height:1.6}
  .footer-left a{color:var(--ha);text-decoration:none}
  .footer-left a:hover{text-decoration:underline}
</style>
</head>
<body>
<h1>
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polygon points="10 8 16 12 10 16 10 8"/></svg>
  DashSnap
</h1>
<div class="subtitle">Screenshot &amp; record any web page — configure targets below</div>

<div class="section-label">Targets</div>
<div id="targets-list"></div>
<button class="add-btn" onclick="openForm(null)">+ Add target</button>

<div class="edit-panel" id="edit-panel" style="display:none">
  <div class="panel-header">
    <h2 id="form-title">New target</h2>
    <button class="panel-close" onclick="closeForm()" title="Cancel">✕</button>
  </div>
  <label>Name</label>
  <input id="f-name" type="text" placeholder="ha">
  <label>Base URL</label>
  <input id="f-url" type="url" placeholder="http://homeassistant.local:8123">
  <div class="hint">URL reachable from within the DashSnap container. Examples: http://homeassistant.local:8123, http://192.168.1.10:8123, or your Nabu Casa URL https://xxxx.ui.nabu.casa.</div>
  <label>Auth strategy</label>
  <select id="f-strat" onchange="onStratChange()">
    <option value="ha_token">ha_token — inject HA long-lived token</option>
    <option value="http_header">http_header — custom request headers</option>
    <option value="none">none — no authentication</option>
  </select>
  <div id="f-token-row">
    <label>Token</label>
    <div id="f-token-saved-box" style="display:none">
      <div class="token-saved-state">
        <span>&#128274; Token saved</span>
        <button type="button" class="replace-btn" onclick="showTokenInput()">Replace</button>
      </div>
      <div class="hint">A token is stored. Click Replace to enter a new one.</div>
    </div>
    <div id="f-token-input-box">
      <input id="f-token" type="password" placeholder="eyJ...">
      <div class="hint">A Home Assistant long-lived access token. Create one in HA → Profile (bottom-left) → Long-lived access tokens → Create token.</div>
    </div>
  </div>
  <div id="f-header-row" style="display:none">
    <label>Headers (JSON object)</label>
    <textarea id="f-headers" rows="2" placeholder='{"Authorization":"Bearer glsa_xxx"}'></textarea>
    <div class="hint">JSON object of HTTP headers to send with every request to this target.</div>
  </div>
  <div class="form-actions">
    <button class="btn-primary" onclick="formSave()">Save target</button>
    <button class="btn-cancel" onclick="closeForm()">Cancel</button>
  </div>
</div>

<button class="save-btn" onclick="save()">Save &amp; Restart</button>
<div class="msg" id="msg"></div>

<footer>
  <div class="footer-left">
    <a href="https://github.com/italo-lombardi/DashSnap" target="_blank" rel="noopener">DashSnap — Screenshot &amp; record any web page via headless Chromium</a>
    &nbsp;·&nbsp; Changes take effect after restart
    &nbsp;·&nbsp; by <a href="https://www.linkedin.com/in/italolombardi/" target="_blank" rel="noopener">Italo Lombardi</a>
    &nbsp;·&nbsp; <a href="https://github.com/italo-lombardi" target="_blank" rel="noopener">more projects</a>
  </div>
</footer>

<script>
let targets = [];
let editIdx = null;

function badgeClass(s) { return s==='ha_token'?'b-ha':s==='http_header'?'b-hdr':'b-none'; }

function render() {
  const list = document.getElementById('targets-list');
  list.innerHTML = '';
  if (!targets.length) {
    list.innerHTML = '<div style="text-align:center;padding:24px;color:var(--muted);font-size:.83rem;border:1px dashed var(--border);border-radius:var(--radius);margin-bottom:8px">No targets configured — add one below</div>';
    return;
  }
  targets.forEach((t, i) => {
    const row = document.createElement('div');
    row.className = 'trow';
    const strat = t.auth ? t.auth.strategy || 'none' : 'none';
    row.innerHTML = `
      <div class="trow-info">
        <div class="trow-name">${esc(t.name || '(unnamed)')}</div>
        <div class="trow-url">${esc(t.base_url || '')}</div>
      </div>
      <span class="badge ${badgeClass(strat)}">${esc(strat)}</span>
      <button class="trow-btn" onclick="openForm(${i})">Edit</button>
      <button class="trow-btn del" onclick="deleteTarget(${i})">Delete</button>`;
    list.appendChild(row);
  });
}

function openForm(idx) {
  editIdx = idx;
  const t = idx !== null ? targets[idx] : {name:'', base_url:'', auth:{strategy:'ha_token', token:'', headers:{}}};
  const strat = (t.auth && t.auth.strategy) || 'ha_token';
  document.getElementById('form-title').textContent = idx !== null ? 'Edit target' : 'New target';
  document.getElementById('f-name').value = t.name || '';
  document.getElementById('f-url').value = t.base_url || '';
  document.getElementById('f-strat').value = strat;
  const tokenSaved = t.auth && t.auth.token === '***';
  document.getElementById('f-token').value = '';
  document.getElementById('f-token').dataset.saved = tokenSaved ? '1' : '';
  document.getElementById('f-token-saved-box').style.display = tokenSaved ? '' : 'none';
  document.getElementById('f-token-input-box').style.display = tokenSaved ? 'none' : '';
  document.getElementById('f-token').placeholder = 'eyJ...';
  document.getElementById('f-headers').value = strat === 'http_header' ? JSON.stringify((t.auth && t.auth.headers) || {}) : '';
  document.getElementById('f-token-row').style.display = strat === 'ha_token' ? '' : 'none';
  document.getElementById('f-header-row').style.display = strat === 'http_header' ? '' : 'none';
  document.getElementById('edit-panel').style.display = '';
  document.getElementById('f-name').focus();
}

function showTokenInput() {
  document.getElementById('f-token-saved-box').style.display = 'none';
  document.getElementById('f-token-input-box').style.display = '';
  document.getElementById('f-token').dataset.saved = '';
  document.getElementById('f-token').focus();
}

function closeForm() {
  document.getElementById('edit-panel').style.display = 'none';
  editIdx = null;
}

function onStratChange() {
  const s = document.getElementById('f-strat').value;
  document.getElementById('f-token-row').style.display = s === 'ha_token' ? '' : 'none';
  document.getElementById('f-header-row').style.display = s === 'http_header' ? '' : 'none';
}

function formSave() {
  const strat = document.getElementById('f-strat').value;
  const auth = {strategy: strat};
  if (strat === 'ha_token') {
    const val = document.getElementById('f-token').value.trim();
    const saved = document.getElementById('f-token').dataset.saved === '1';
    if (val) auth.token = val;
    else if (saved) auth.token = '***';
  }
  if (strat === 'http_header') {
    try { auth.headers = JSON.parse(document.getElementById('f-headers').value || '{}'); }
    catch(e) { alert('Invalid JSON in headers: ' + e.message); return; }
  }
  const t = {name: document.getElementById('f-name').value.trim(), base_url: document.getElementById('f-url').value.trim(), auth};
  if (editIdx !== null) targets[editIdx] = t; else targets.push(t);
  render();
  closeForm();
}

function deleteTarget(i) { targets.splice(i, 1); render(); }

function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

async function save() {
  const btn = document.querySelector('.save-btn');
  const msg = document.getElementById('msg');
  msg.className = 'msg'; msg.textContent = '';
  try {
    const payload = {base_url:'', token:'', targets_json: JSON.stringify(targets)};
    btn.disabled = true; btn.textContent = 'Saving…';
    const r = await fetch('config', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'save failed');
    msg.className = 'msg ok';
    msg.textContent = j.restarting ? 'Saved. Restarting addon…' : 'Saved. Configuration applied.';
  } catch(e) {
    msg.className = 'msg err'; msg.textContent = e.message;
  } finally {
    btn.disabled = false; btn.textContent = btn.dataset.label || 'Save';
  }
}

(async () => {
  try {
    const r = await fetch('config');
    const j = await r.json();
    if (j.targets_json) {
      try { targets = JSON.parse(j.targets_json); }
      catch(e) {
        const msg = document.getElementById('msg');
        msg.className = 'msg err'; msg.textContent = 'Stored targets_json is invalid: ' + e.message;
      }
    } else if (j.base_url) {
      targets = [{name:'default', base_url:j.base_url, auth:{strategy:'ha_token', token: j.token || ''}}];
    }
    render();
    document.querySelector('.save-btn').textContent = j.has_supervisor ? 'Save & Restart' : 'Save';
    document.querySelector('.save-btn').dataset.label = j.has_supervisor ? 'Save & Restart' : 'Save';
  } catch(e) {
    const msg = document.getElementById('msg');
    msg.className = 'msg err'; msg.textContent = 'Failed to load config: ' + e.message;
  }
})();
</script>
</body>
</html>"""

_SUPERVISOR_URL = "http://supervisor/addons/self/options"


async def handle_config_ui(request):
    """GET / — serve the ingress config page."""
    return web.Response(text=_CONFIG_UI, content_type="text/html")


async def handle_config_get(request):
    """GET /config — return current options.json for the UI to pre-populate."""
    cfg_path = os.environ.get("CONFIG_PATH", "/data/options.json")
    try:
        with open(cfg_path) as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    return web.json_response(
        {
            "ok": True,
            "base_url": data.get("base_url", ""),
            "token": "***" if data.get("token") else "",
            "targets_json": data.get("targets_json", ""),
            "has_supervisor": bool(os.environ.get("SUPERVISOR_TOKEN")),
        }
    )


async def handle_config_save(request):
    """POST /config — write options via Supervisor API (HA) or directly to options.json (dev)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON body"}, status=400)

    allowed = {"base_url", "token", "targets_json"}
    options = {k: v for k, v in body.items() if k in allowed}
    if not options.get("token") or options["token"] == "***":
        options.pop("token", None)
    if options.get("targets_json"):
        try:
            json.loads(options["targets_json"])
        except json.JSONDecodeError as e:
            return web.json_response({"ok": False, "error": f"targets_json: {e}"}, status=400)

    supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not supervisor_token:
        # Dev / local mode — write directly to options.json
        cfg_path = os.environ.get("CONFIG_PATH", "/data/options.json")
        try:
            try:
                with open(cfg_path) as f:
                    existing = json.load(f)
            except FileNotFoundError:
                existing = {}
            existing.update(options)
            with open(cfg_path, "w") as f:
                json.dump(existing, f, indent=2)
            _load_config()
        except Exception as e:
            log.error("config save (local) failed: %s", e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        return web.json_response({"ok": True})

    headers = {"Authorization": f"Bearer {supervisor_token}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                _SUPERVISOR_URL,
                json={"options": options},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    text = await r.text()
                    return web.json_response(
                        {"ok": False, "error": f"supervisor returned {r.status}: {text}"},
                        status=502,
                    )
            # restart addon so new config takes effect
            async with s.post(
                "http://supervisor/addons/self/restart",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                pass  # pragma: no cover — best-effort, connection drops on restart
    except aiohttp.ClientConnectionError:
        pass  # expected — addon restarted mid-request
    except Exception as e:
        log.error("config save failed: %s", e)
        return web.json_response({"ok": False, "error": str(e)}, status=502)

    return web.json_response({"ok": True})


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = web.Application()
app.router.add_get("/", handle_config_ui)
app.router.add_get("/config", handle_config_get)
app.router.add_post("/config", handle_config_save)
app.router.add_route("*", "/record", handle_record)
app.router.add_route("*", "/record/ha", handle_record_ha)
app.router.add_get("/health", handle_health)
app.router.add_get("/targets", handle_targets)
app.router.add_get("/ha/dashboards", handle_ha_dashboards)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = int(os.environ.get("INGRESS_PORT", 8099))
    log.info("DashSnap starting on port %d", port)
    log.info("Configured targets: %s", list(TARGETS.keys()) if TARGETS else "none")
    log.info("Default target: %s", DEFAULT_TARGET)
    web.run_app(app, host="0.0.0.0", port=port, access_log=logging.getLogger("aiohttp.access"))
