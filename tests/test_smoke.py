"""End-to-end smoke test — launches a real headless Chromium.

Everything in test_record.py mocks the browser away; this is the only test
that proves record() actually drives Chromium and writes a file. It exercises
the exact path that broke in #53 (context/browser lifecycle) plus the 0.1.16
asyncio.wait_for wrapper.

Skipped unless chromium is installed AND the target URL is reachable, so a
missing browser or a network/site outage downgrades to a skip rather than a
red job. Run it with `pytest -m smoke` and a separate `--no-cov` — the 100%
coverage gate is scoped to the mocked unit suite, not this real-browser path.
"""

import pathlib
import urllib.request

import pytest

import record

# Live page. Note: this makes the smoke job depend on the site + network.
# _url_reachable() below downgrades an outage to a skip, not a failure, so a
# site/network blip can't turn the job red and block PRs.
_URL = "http://www.infoshare.it/hp/?n=dashsnap"


def _chromium_available():
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


def _url_reachable():
    try:
        with urllib.request.urlopen(_URL, timeout=10) as r:  # noqa: S310
            return r.status < 500
    except Exception:
        return False


@pytest.mark.smoke
@pytest.mark.skipif(not _chromium_available(), reason="chromium not installed")
@pytest.mark.skipif(not _url_reachable(), reason=f"{_URL} unreachable")
@pytest.mark.asyncio
async def test_png_screenshot_written(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "OUT_DIR", pathlib.Path(tmp_path))
    monkeypatch.setattr(
        record,
        "TARGETS",
        {"public": {"name": "public", "base_url": "", "auth": {"strategy": "none"}}},
    )
    monkeypatch.setattr(record, "DEFAULT_TARGET", "public")

    out = await record.record(
        _URL, seconds=0, vw=1920, vh=1080, fmt="png", target_name="public", delay=2
    )

    f = pathlib.Path(out)
    assert f.exists(), "record() returned a path that was not written"
    assert f.suffix == ".png"
    # A real screenshot is kilobytes; an empty/placeholder file would be tiny.
    assert f.stat().st_size > 1000, f"screenshot suspiciously small: {f.stat().st_size} bytes"
    assert f.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "output is not a valid PNG"
