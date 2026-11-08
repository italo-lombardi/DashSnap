"""Live HTTP smoke for the /recordings list+delete endpoints.

Unlike test_smoke.py (which launches Chromium in-process), this drives the
REAL running container over HTTP — the same path HA's integration uses. It
records one uniquely-named clip, then lists / dry-runs / deletes ONLY that
exact filename, and asserts the three error paths (refuse_wipe, not_found,
invalid_name).

Deliberately NOT skipif-guarded: if the container is down the test FAILS, it
does not skip. (The feature's acceptance bar is "0 skipped"; a silent skip
would hide a broken endpoint.) Start the container first:

    docker compose up -d --build
    pytest -m smoke --no-cov tests/test_smoke_recordings.py

Safety: every mutation is scoped to the exact timestamped filename this test
created. It never sends slug= / older_than_days= / all=, so it cannot touch
any other recording in the shared OUT_DIR.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

BASE = "http://127.0.0.1:8099"  # not "localhost": pytest-socket allows 127.0.0.1, not ::1
# A distinctive path so the sanitized slug ("hp") plus the per-second timestamp
# is ours alone; we still scope deletes to the full filename, never the slug.
_REC_URL = "http://www.infoshare.it/hp/?n=dashsnap-smoke"


def _req(method, path, params=None, timeout=120):
    url = f"{BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())  # error bodies are JSON too


def _container_up():
    try:
        status, _ = _req("GET", "/health", timeout=10)
        return status == 200
    except Exception as e:  # noqa: BLE001 — any failure means "not up"; surface it
        pytest.fail(
            f"DashSnap container not reachable at {BASE} ({e!r}). "
            "Start it with `docker compose up -d --build` before running the smoke."
        )


@pytest.mark.smoke
def test_recordings_live_round_trip(socket_enabled):
    # socket_enabled (pytest-socket fixture): the HA test plugin disables real
    # sockets globally; this live smoke needs them. Scoped to this test only.
    _container_up()

    # 1. record a uniquely-named clip
    status, data = _req("GET", "/record", {"url": _REC_URL, "format": "png", "target": "public"})
    assert status == 200 and data["ok"], f"record failed: {data}"
    name = data["file"].rsplit("/", 1)[-1]
    assert name.endswith(".png")

    try:
        # 2. it appears in the listing
        status, data = _req("GET", "/recordings")
        assert status == 200 and data["ok"]
        assert name in [r["name"] for r in data["recordings"]], "new recording missing from list"

        # 3. dry-run delete by exact name: reported, NOT removed
        status, data = _req("POST", "/recordings/delete", {"name": name, "dry_run": "true"})
        assert status == 200 and data["deleted"] == [name] and data["dry_run"] is True
        status, data = _req("GET", "/recordings")
        assert name in [r["name"] for r in data["recordings"]], "dry_run must not delete"

        # 4. real delete by exact name
        status, data = _req("POST", "/recordings/delete", {"name": name})
        assert status == 200 and data["deleted"] == [name] and data["dry_run"] is False

        # 5. gone
        status, data = _req("GET", "/recordings")
        assert name not in [r["name"] for r in data["recordings"]], "file still present after delete"
    finally:
        # Best-effort cleanup if an assert above left the file behind.
        _req("POST", "/recordings/delete", {"name": name})

    # 6-8. error paths — the locked contract slugs
    status, data = _req("POST", "/recordings/delete")
    assert status == 400 and data["error"] == "refuse_wipe"

    status, data = _req("POST", "/recordings/delete", {"name": "20260101_000000_nope.png"})
    assert status == 404 and data["error"] == "not_found"

    status, data = _req("POST", "/recordings/delete", {"name": "badname.txt"})
    assert status == 400 and data["error"] == "invalid_name"
