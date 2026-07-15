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

_cfg_path = os.environ.get("CONFIG_PATH", "/data/options.json")
try:
    with open(_cfg_path) as _f:
        CFG = json.load(_f)  # pragma: no cover
except FileNotFoundError:
    CFG = {}

# Env var overrides (single-target convenience — wraps into targets list below)
if os.environ.get("DASHSNAP_BASE_URL"):  # pragma: no cover
    CFG["base_url"] = os.environ["DASHSNAP_BASE_URL"]
if (
    os.environ.get("DASHSNAP_AUTH_STRATEGY")
    or os.environ.get("DASHSNAP_AUTH_TOKEN")
    or os.environ.get("DASHSNAP_AUTH_HEADERS")
):  # pragma: no cover
    auth = CFG.setdefault("auth", {})
    if os.environ.get("DASHSNAP_AUTH_STRATEGY"):
        auth["strategy"] = os.environ["DASHSNAP_AUTH_STRATEGY"]
    if os.environ.get("DASHSNAP_AUTH_TOKEN"):
        s = auth.get("strategy", "ha_token")
        if s == "http_header":
            auth.setdefault("headers", {})["Authorization"] = (
                f"Bearer {os.environ['DASHSNAP_AUTH_TOKEN']}"
            )
        else:
            auth["token"] = os.environ["DASHSNAP_AUTH_TOKEN"]
    if os.environ.get("DASHSNAP_AUTH_HEADERS"):
        try:
            auth["headers"] = json.loads(os.environ["DASHSNAP_AUTH_HEADERS"])
        except json.JSONDecodeError as e:
            raise SystemExit(f"DASHSNAP_AUTH_HEADERS is not valid JSON: {e}") from e

# Backward-compat + multi-target shim (pragma: no cover — runs at import from config file)
if "targets" not in CFG:  # pragma: no cover
    targets_json = CFG.get("targets_json", "").strip()
    if targets_json:
        try:
            CFG["targets"] = json.loads(targets_json)
        except json.JSONDecodeError as e:
            raise SystemExit(f"targets_json is not valid JSON: {e}") from e
    elif "base_url" in CFG:
        if "token" in CFG and "auth" not in CFG:
            CFG["auth"] = {"strategy": "ha_token", "token": CFG["token"]}
        CFG["targets"] = [
            {
                "name": "default",
                "base_url": CFG["base_url"],
                "auth": CFG.get("auth", {"strategy": "ha_token"}),
            }
        ]

TARGETS = {t["name"]: t for t in CFG.get("targets", [])}
DEFAULT_TARGET = next(iter(TARGETS)) if TARGETS else None

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
  :root { --ha: #03a9f4; --bg: #1c1c1c; --card: #2a2a2a; --border: #3a3a3a; --text: #e0e0e0; --muted: #888; --err: #f44336; --ok: #4caf50; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: var(--bg); color: var(--text); padding: 24px; }
  h1 { font-size: 1.3rem; font-weight: 600; color: var(--ha); margin-bottom: 20px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 16px; }
  label { display: block; font-size: .8rem; color: var(--muted); margin-bottom: 4px; margin-top: 12px; }
  label:first-child { margin-top: 0; }
  input, select, textarea { width: 100%; background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 4px; padding: 8px 10px; font-size: .9rem; font-family: inherit; }
  textarea { font-family: monospace; resize: vertical; min-height: 80px; }
  input:focus, select:focus, textarea:focus { outline: none; border-color: var(--ha); }
  .target-card { border: 1px solid var(--border); border-radius: 6px; padding: 14px; margin-bottom: 10px; position: relative; }
  .target-card h3 { font-size: .85rem; color: var(--muted); margin-bottom: 10px; }
  .remove-btn { position: absolute; top: 10px; right: 10px; background: none; border: none; color: var(--muted); cursor: pointer; font-size: 1.1rem; padding: 0 4px; }
  .remove-btn:hover { color: var(--err); }
  .add-btn { width: 100%; padding: 8px; border: 1px dashed var(--border); border-radius: 4px; background: none; color: var(--muted); cursor: pointer; font-size: .85rem; margin-top: 4px; }
  .add-btn:hover { border-color: var(--ha); color: var(--ha); }
  .save-btn { width: 100%; padding: 10px; background: var(--ha); color: #fff; border: none; border-radius: 4px; font-size: .95rem; cursor: pointer; margin-top: 8px; font-weight: 600; }
  .save-btn:hover { opacity: .9; }
  .save-btn:disabled { opacity: .5; cursor: default; }
  .msg { padding: 10px 14px; border-radius: 4px; margin-top: 12px; font-size: .85rem; display: none; }
  .msg.ok { background: rgba(76,175,80,.15); border: 1px solid var(--ok); color: var(--ok); display: block; }
  .msg.err { background: rgba(244,67,54,.15); border: 1px solid var(--err); color: var(--err); display: block; }
  .hint { font-size: .75rem; color: var(--muted); margin-top: 4px; }
  .header-row { display: flex; gap: 8px; }
  .header-row input:first-child { flex: 1; }
  .header-row input:last-child { flex: 2; }
</style>
</head>
<body>
<h1>DashSnap — Configure</h1>

<div class="card">
  <div id="targets-list"></div>
  <button class="add-btn" onclick="addTarget()">+ Add target</button>

  <button class="save-btn" onclick="save()">Save &amp; Restart</button>
  <div class="msg" id="msg"></div>
</div>

<script>
const STRATEGIES = ['ha_token','http_header','none'];

function addTarget(t) {
  t = t || {name:'', base_url:'', auth:{strategy:'ha_token', token:'', headers:{}}};
  const strat = t.auth.strategy || 'ha_token';
  const headerVal = strat === 'http_header' ? JSON.stringify(t.auth.headers || {}) : '';
  const tokenVal = t.auth.token || '';
  const id = 'tgt-' + Date.now() + Math.random().toString(36).slice(2);
  const div = document.createElement('div');
  div.className = 'target-card';
  div.id = id;
  div.innerHTML = `
    <h3>Target</h3>
    <button class="remove-btn" onclick="document.getElementById('${id}').remove()">✕</button>
    <label>Name</label>
    <input class="t-name" type="text" placeholder="ha" value="${esc(t.name)}">
    <label>Base URL</label>
    <input class="t-url" type="url" placeholder="http://homeassistant.local:8123" value="${esc(t.base_url)}">
    <label>Auth strategy</label>
    <select class="t-strat" onchange="onStratChange(this)">
      ${STRATEGIES.map(s => `<option value="${s}"${s===strat?' selected':''}>${s}</option>`).join('')}
    </select>
    <div class="t-token-row" style="display:${strat==='ha_token'?'':'none'}">
      <label>Token</label>
      <input class="t-token" type="password" placeholder="eyJ..." value="${esc(tokenVal)}">
    </div>
    <div class="t-header-row" style="display:${strat==='http_header'?'':'none'}">
      <label>Headers (JSON object)</label>
      <textarea class="t-headers" rows="2" placeholder='{"Authorization":"Bearer glsa_xxx"}'>${esc(headerVal)}</textarea>
    </div>`;
  document.getElementById('targets-list').appendChild(div);
}

function onStratChange(sel) {
  const card = sel.closest('.target-card');
  card.querySelector('.t-token-row').style.display = sel.value === 'ha_token' ? '' : 'none';
  card.querySelector('.t-header-row').style.display = sel.value === 'http_header' ? '' : 'none';
}

function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function buildPayload() {
  const cards = document.querySelectorAll('#targets-list .target-card');
  const targets = [];
  for (const c of cards) {
    const strat = c.querySelector('.t-strat').value;
    const auth = {strategy: strat};
    if (strat === 'ha_token') auth.token = c.querySelector('.t-token').value.trim();
    if (strat === 'http_header') {
      try { auth.headers = JSON.parse(c.querySelector('.t-headers').value || '{}'); }
      catch { throw new Error('Invalid JSON in headers for target "' + c.querySelector('.t-name').value + '"'); }
    }
    targets.push({name: c.querySelector('.t-name').value.trim(), base_url: c.querySelector('.t-url').value.trim(), auth});
  }
  return {base_url: '', token: '', targets_json: JSON.stringify(targets)};
}

async function save() {
  const btn = document.querySelector('.save-btn');
  const msg = document.getElementById('msg');
  msg.className = 'msg'; msg.textContent = '';
  try {
    const payload = buildPayload();
    btn.disabled = true; btn.textContent = 'Saving…';
    const r = await fetch('config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'save failed');
    msg.className = 'msg ok'; msg.textContent = 'Saved. Restarting addon…';
  } catch(e) {
    msg.className = 'msg err'; msg.textContent = e.message;
  } finally {
    btn.disabled = false; btn.textContent = 'Save & Restart';
  }
}

// Load current config on page load
(async () => {
  try {
    const r = await fetch('config');
    const j = await r.json();
    if (j.targets_json) {
      try {
        JSON.parse(j.targets_json).forEach(addTarget);
      } catch(e) {
        const msg = document.getElementById('msg');
        msg.className = 'msg err'; msg.textContent = 'Stored targets_json is invalid: ' + e.message;
      }
    } else if (j.base_url) {
      addTarget({name:'default', base_url: j.base_url, auth:{strategy:'ha_token', token: j.token === '***' ? '' : (j.token || ''), headers:{}}});
    }
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
        }
    )


async def handle_config_save(request):
    """POST /config — write options via Supervisor API then restart."""
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not supervisor_token:
        return web.json_response(
            {
                "ok": False,
                "error": "SUPERVISOR_TOKEN not available — not running under HA supervisor",
            },
            status=503,
        )
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
