import asyncio
import json
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
        CFG = json.load(_f)
except FileNotFoundError:
    CFG = {}

# Env var overrides (single-target convenience — wraps into targets list below)
if os.environ.get("DASHSNAP_BASE_URL"):
    CFG["base_url"] = os.environ["DASHSNAP_BASE_URL"]
if os.environ.get("DASHSNAP_AUTH_STRATEGY") or os.environ.get("DASHSNAP_AUTH_TOKEN") or os.environ.get("DASHSNAP_AUTH_HEADERS"):
    auth = CFG.setdefault("auth", {})
    if os.environ.get("DASHSNAP_AUTH_STRATEGY"):
        auth["strategy"] = os.environ["DASHSNAP_AUTH_STRATEGY"]
    if os.environ.get("DASHSNAP_AUTH_TOKEN"):
        s = auth.get("strategy", "ha_token")
        if s == "http_header":
            auth.setdefault("headers", {})["Authorization"] = f"Bearer {os.environ['DASHSNAP_AUTH_TOKEN']}"
        else:
            auth["token"] = os.environ["DASHSNAP_AUTH_TOKEN"]
    if os.environ.get("DASHSNAP_AUTH_HEADERS"):
        try:
            auth["headers"] = json.loads(os.environ["DASHSNAP_AUTH_HEADERS"])
        except json.JSONDecodeError as e:
            raise SystemExit(f"DASHSNAP_AUTH_HEADERS is not valid JSON: {e}") from e

# Backward-compat: old flat {base_url, token} or {base_url, auth} → single target "default"
if "base_url" in CFG and "targets" not in CFG:
    if "token" in CFG and "auth" not in CFG:
        CFG["auth"] = {"strategy": "ha_token", "token": CFG["token"]}
    CFG["targets"] = [{"name": "default", "base_url": CFG["base_url"],
                       "auth": CFG.get("auth", {"strategy": "ha_token"})}]

TARGETS = {t["name"]: t for t in CFG.get("targets", [])}
DEFAULT_TARGET = next(iter(TARGETS)) if TARGETS else None

OUT_DIR = pathlib.Path(os.environ.get("OUT_DIR", "/media/dashsnap"))

DEFAULTS = {
    "seconds": 30,
    "viewport_width": 1920,
    "viewport_height": 1080,
}

# ---------------------------------------------------------------------------
# Auth strategies
# ---------------------------------------------------------------------------

async def _auth_none(context, page, auth_cfg, base_url):
    pass


async def _auth_http_header(context, page, auth_cfg, base_url):
    headers = auth_cfg.get("headers", {})
    if headers:
        await context.set_extra_http_headers(headers)


async def _auth_ha_token(context, page, auth_cfg, base_url):
    token = auth_cfg.get("token", "")
    token_blob = json.dumps({
        "access_token": token, "token_type": "Bearer", "expires_in": 1800,
        "hassUrl": base_url, "clientId": base_url + "/",
        "expires": 9999999999999, "refresh_token": "",
    })
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

async def record(url, seconds, vw, vh, fmt="webm", target_name=None):
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
    tag = re.sub(r"[^a-zA-Z0-9]+", "_", url.split("://")[-1].strip("/")) or "page"
    is_png = fmt == "png"
    tmp_dir = OUT_DIR / f".tmp_{tag}_{stamp}"
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
                except Exception:
                    pass
                if await page.query_selector("input[name='username']") or \
                   not await page.query_selector("home-assistant"):
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
                async with s.get(f"{base_url}/api/",
                                 headers={"Authorization": f"Bearer {token}"},
                                 timeout=aiohttp.ClientTimeout(total=10)) as r:
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
                    return {"name": name, "ok": ok, "strategy": strategy,
                            "base_url": base_url, "http_status": r.status}
    except Exception as e:
        return {"name": name, "ok": False, "strategy": strategy, "base_url": base_url, "error": str(e)}

# ---------------------------------------------------------------------------
# HA helpers
# ---------------------------------------------------------------------------

def _ha_target():
    """Return the target named 'ha', else first ha_token target, else None."""
    if "ha" in TARGETS:
        return TARGETS["ha"]
    return next((t for t in TARGETS.values() if t.get("auth", {}).get("strategy") == "ha_token"), None)


async def list_dashboards():
    target = _ha_target()
    if target is None:
        raise RuntimeError("no ha_token target configured")
    base_url = target["base_url"].rstrip("/")
    token = target.get("auth", {}).get("token", "")
    ws_url = base_url.replace("http", "ws", 1) + "/api/websocket"
    async with aiohttp.ClientSession() as s:
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
        return web.json_response({"ok": False, "error": str(e)}, status=500)
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
        return web.json_response({"ok": False, "error": f"unknown target: {target_name!r}"}, status=400)
    base = target["base_url"].rstrip("/")
    url = base + ("" if path.startswith("/") else "/") + path
    try:
        out = await record(url, p["seconds"], p["vw"], p["vh"], p["fmt"], target_name)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
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
        return web.json_response({"ok": False, "error": "no ha_token target configured"}, status=404)
    try:
        dashboards = await list_dashboards()
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=502)
    return web.json_response({"ok": True, "dashboards": dashboards})

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = web.Application()
app.router.add_route("*", "/record", handle_record)
app.router.add_route("*", "/record/ha", handle_record_ha)
app.router.add_get("/health", handle_health)
app.router.add_get("/targets", handle_targets)
app.router.add_get("/ha/dashboards", handle_ha_dashboards)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8099)
