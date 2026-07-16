"""Tests for record.py — HTTP handlers and param parsing.

All handler tests use make_mocked_request — no real server, no sockets, no threads.
_check_target_health tests use aioresponses to mock HTTP.
Playwright is never launched.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

sys.path.insert(0, str(Path(__file__).parent.parent))

HA_TARGET = {
    "name": "ha",
    "base_url": "http://homeassistant.local:8123",
    "auth": {"strategy": "ha_token", "token": "tok123"},
}
NONE_TARGET = {
    "name": "public",
    "base_url": "https://example.com",
    "auth": {"strategy": "none"},
}
HEADER_TARGET = {
    "name": "grafana",
    "base_url": "https://grafana.example.com",
    "auth": {"strategy": "http_header", "headers": {"Authorization": "Bearer abc"}},
}


def _req(method="GET", path="/record", params=None):
    """Build a mocked aiohttp request with query string."""
    from multidict import MultiDict

    qs = MultiDict(params or {})
    req = make_mocked_request(method, path, headers={"Host": "localhost"})
    req = req.clone(rel_url=req.rel_url.with_query(qs))
    return req


# ---------------------------------------------------------------------------
# _params
# ---------------------------------------------------------------------------


class TestParams:
    def test_defaults(self):
        import record

        p = record._params({})
        assert p["seconds"] == 30
        assert p["vw"] == 1920
        assert p["vh"] == 1080
        assert p["fmt"] == "webm"
        assert p["target_name"] is None

    def test_custom_values(self):
        import record

        q = {
            "seconds": "10",
            "viewport_width": "1280",
            "viewport_height": "720",
            "format": "png",
            "target": "ha",
        }
        p = record._params(q)
        assert p["seconds"] == 10
        assert p["vw"] == 1280
        assert p["vh"] == 720
        assert p["fmt"] == "png"
        assert p["target_name"] == "ha"

    def test_seconds_capped_at_3600(self):
        import record

        assert record._params({"seconds": "99999"})["seconds"] == 3600

    def test_invalid_seconds_raises_400(self):
        import record

        with pytest.raises(web.HTTPBadRequest):
            record._params({"seconds": "notanumber"})

    def test_invalid_format_falls_back_to_webm(self):
        import record

        assert record._params({"format": "gif"})["fmt"] == "webm"

    def test_empty_target_returns_none(self):
        import record

        assert record._params({"target": ""})["target_name"] is None

    def test_delay_default_zero(self):
        import record

        assert record._params({})["delay"] == 0

    def test_delay_custom(self):
        import record

        assert record._params({"delay": "10"})["delay"] == 10

    def test_delay_capped_at_60(self):
        import record

        # delay=999 with seconds=3600 → capped at 60 (60 < 3600-1=3599)
        assert record._params({"delay": "999", "seconds": "3600"})["delay"] == 60

    def test_delay_capped_to_seconds_minus_one(self):
        import record

        # delay=60 with seconds=10 → capped to 9 (seconds-1)
        assert record._params({"delay": "60", "seconds": "10"})["delay"] == 9

    def test_delay_negative_clamped_to_zero(self):
        import record

        assert record._params({"delay": "-5"})["delay"] == 0

    def test_seconds_minimum_one(self):
        import record

        assert record._params({"seconds": "0"})["seconds"] == 1


# ---------------------------------------------------------------------------
# /record
# ---------------------------------------------------------------------------


class TestHandleRecord:
    async def test_missing_url_returns_400(self):
        import record

        with (
            patch.object(record, "TARGETS", {"public": NONE_TARGET}),
            patch.object(record, "DEFAULT_TARGET", "public"),
        ):
            req = _req(params={})
            resp = await record.handle_record(req)
            assert resp.status == 400
            data = record.json.loads(resp.body)
            assert not data["ok"]

    async def test_file_scheme_rejected(self):
        import record

        with (
            patch.object(record, "TARGETS", {"public": NONE_TARGET}),
            patch.object(record, "DEFAULT_TARGET", "public"),
        ):
            req = _req(params={"url": "file:///etc/passwd"})
            resp = await record.handle_record(req)
            assert resp.status == 400
            data = record.json.loads(resp.body)
            assert "http" in data["error"]

    async def test_javascript_scheme_rejected(self):
        import record

        with (
            patch.object(record, "TARGETS", {"public": NONE_TARGET}),
            patch.object(record, "DEFAULT_TARGET", "public"),
        ):
            req = _req(params={"url": "javascript:alert(1)"})
            resp = await record.handle_record(req)
            assert resp.status == 400

    async def test_valid_url_calls_record(self):
        import record

        with (
            patch.object(record, "TARGETS", {"public": NONE_TARGET}),
            patch.object(record, "DEFAULT_TARGET", "public"),
            patch.object(record, "record", AsyncMock(return_value="/media/DashSnap/out.png")),
        ):
            req = _req(params={"url": "https://example.com", "format": "png"})
            resp = await record.handle_record(req)
            assert resp.status == 200
            data = record.json.loads(resp.body)
            assert data["ok"]
            assert data["file"] == "/media/DashSnap/out.png"
            assert data["url"] == "https://example.com"
            assert data["format"] == "png"
            assert data["target"] == "public"

    async def test_record_exception_returns_500(self):
        import record

        with (
            patch.object(record, "TARGETS", {"public": NONE_TARGET}),
            patch.object(record, "DEFAULT_TARGET", "public"),
            patch.object(record, "record", AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            req = _req(params={"url": "https://example.com"})
            resp = await record.handle_record(req)
            assert resp.status == 500
            data = record.json.loads(resp.body)
            assert not data["ok"]
            assert "boom" in data["error"]


# ---------------------------------------------------------------------------
# /record/ha
# ---------------------------------------------------------------------------


class TestHandleRecordHa:
    async def test_missing_path_returns_400(self):
        import record

        with (
            patch.object(record, "TARGETS", {"ha": HA_TARGET}),
            patch.object(record, "DEFAULT_TARGET", "ha"),
        ):
            req = _req(path="/record/ha", params={})
            resp = await record.handle_record_ha(req)
            assert resp.status == 400

    async def test_empty_base_url_target_returns_400(self):
        import record

        no_base = {"name": "public", "base_url": "", "auth": {"strategy": "none"}}
        with (
            patch.object(record, "TARGETS", {"public": no_base}),
            patch.object(record, "DEFAULT_TARGET", "public"),
        ):
            req = _req(path="/record/ha", params={"path": "/lovelace/0"})
            resp = await record.handle_record_ha(req)
            assert resp.status == 400
            assert "base_url" in record.json.loads(resp.body)["error"]

    async def test_unknown_target_returns_400(self):
        import record

        with (
            patch.object(record, "TARGETS", {"ha": HA_TARGET}),
            patch.object(record, "DEFAULT_TARGET", "ha"),
        ):
            req = _req(path="/record/ha", params={"path": "/lovelace/0", "target": "nonexistent"})
            resp = await record.handle_record_ha(req)
            assert resp.status == 400

    async def test_path_prepends_base_url(self):
        import record

        mock_record = AsyncMock(return_value="/media/DashSnap/out.png")
        with (
            patch.object(record, "TARGETS", {"ha": HA_TARGET}),
            patch.object(record, "DEFAULT_TARGET", "ha"),
            patch.object(record, "record", mock_record),
        ):
            req = _req(path="/record/ha", params={"path": "/lovelace/0", "target": "ha"})
            resp = await record.handle_record_ha(req)
            assert resp.status == 200
            data = record.json.loads(resp.body)
            assert data["ok"]
            assert data["file"] == "/media/DashSnap/out.png"
            assert data["target"] == "ha"
            assert data["url"] == "http://homeassistant.local:8123/lovelace/0"
            assert data["format"] == "webm"
            assert mock_record.call_args[0][0] == "http://homeassistant.local:8123/lovelace/0"

    async def test_path_without_leading_slash(self):
        import record

        mock_record = AsyncMock(return_value="/media/DashSnap/out.png")
        with (
            patch.object(record, "TARGETS", {"ha": HA_TARGET}),
            patch.object(record, "DEFAULT_TARGET", "ha"),
            patch.object(record, "record", mock_record),
        ):
            req = _req(path="/record/ha", params={"path": "lovelace/0", "target": "ha"})
            resp = await record.handle_record_ha(req)
            assert resp.status == 200
            assert mock_record.call_args[0][0] == "http://homeassistant.local:8123/lovelace/0"

    async def test_bad_base_url_in_target_returns_400(self):
        import record

        bad_target = {**HA_TARGET, "base_url": "ftp://bad.host"}
        with (
            patch.object(record, "TARGETS", {"ha": bad_target}),
            patch.object(record, "DEFAULT_TARGET", "ha"),
        ):
            req = _req(path="/record/ha", params={"path": "/lovelace/0", "target": "ha"})
            resp = await record.handle_record_ha(req)
            assert resp.status == 400

    async def test_record_exception_returns_500(self):
        import record

        with (
            patch.object(record, "TARGETS", {"ha": HA_TARGET}),
            patch.object(record, "DEFAULT_TARGET", "ha"),
            patch.object(record, "record", AsyncMock(side_effect=RuntimeError("nav failed"))),
        ):
            req = _req(path="/record/ha", params={"path": "/lovelace/0", "target": "ha"})
            resp = await record.handle_record_ha(req)
            assert resp.status == 500


# ---------------------------------------------------------------------------
# /targets
# ---------------------------------------------------------------------------


class TestHandleTargets:
    async def test_returns_names_and_strategies(self):
        import record

        targets = {"ha": HA_TARGET, "public": NONE_TARGET}
        with patch.object(record, "TARGETS", targets):
            req = _req(path="/targets")
            resp = await record.handle_targets(req)
            assert resp.status == 200
            data = record.json.loads(resp.body)
            assert data["ok"]
            names = {t["name"]: t["strategy"] for t in data["targets"]}
            assert names == {"ha": "ha_token", "public": "none"}

    async def test_empty_targets(self):
        import record

        with patch.object(record, "TARGETS", {}):
            req = _req(path="/targets")
            resp = await record.handle_targets(req)
            assert resp.status == 200
            data = record.json.loads(resp.body)
            assert data["targets"] == []


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


class TestSelfIps:
    def test_returns_ipv4_non_loopback(self):
        import record

        with patch.object(
            record.socket,
            "getaddrinfo",
            return_value=[
                (record.socket.AF_INET, None, None, None, ("192.168.1.10", 0)),
                (record.socket.AF_INET, None, None, None, ("127.0.0.1", 0)),
                (record.socket.AF_INET6, None, None, None, ("::1", 0)),
            ],
        ):
            ips = record._self_ips()
        assert ips == ["192.168.1.10"]

    def test_returns_empty_on_oserror(self):
        import record

        with patch.object(record.socket, "getaddrinfo", side_effect=OSError("no network")):
            assert record._self_ips() == []


class TestHandleHealth:
    async def test_all_healthy_returns_200(self):
        import record

        healthy = {"name": "ha", "ok": True, "strategy": "ha_token", "probed": True}
        with (
            patch.object(record, "TARGETS", {"ha": HA_TARGET}),
            patch.object(record, "_check_target_health", AsyncMock(return_value=healthy)),
        ):
            req = _req(path="/health")
            resp = await record.handle_health(req)
            assert resp.status == 200
            data = record.json.loads(resp.body)
            assert data["ok"]

    async def test_unhealthy_target_returns_200(self):
        import record

        unhealthy = {
            "name": "ha",
            "ok": False,
            "strategy": "ha_token",
            "probed": True,
            "error": "refused",
        }
        with (
            patch.object(record, "TARGETS", {"ha": HA_TARGET}),
            patch.object(record, "_check_target_health", AsyncMock(return_value=unhealthy)),
        ):
            req = _req(path="/health")
            resp = await record.handle_health(req)
            assert resp.status == 200
            data = record.json.loads(resp.body)
            assert not data["ok"]

    async def test_no_targets_returns_200(self):
        import record

        with patch.object(record, "TARGETS", {}):
            req = _req(path="/health")
            resp = await record.handle_health(req)
            assert resp.status == 200

    async def test_self_urls_included_in_response(self):
        import record

        healthy = {"name": "ha", "ok": True, "strategy": "ha_token", "probed": True}
        with (
            patch.object(record, "TARGETS", {"ha": HA_TARGET}),
            patch.object(record, "_check_target_health", AsyncMock(return_value=healthy)),
            patch.object(record, "_self_ips", return_value=["192.168.1.10"]),
        ):
            req = _req(path="/health")
            resp = await record.handle_health(req)
            data = record.json.loads(resp.body)
            assert "self_urls" in data
            assert any("192.168.1.10" in u for u in data["self_urls"])
            assert not any("127.0.0.1" in u for u in data["self_urls"])

    async def test_self_urls_empty_on_getaddrinfo_error(self):
        import record

        healthy = {"name": "ha", "ok": True, "strategy": "ha_token", "probed": True}
        with (
            patch.object(record, "TARGETS", {"ha": HA_TARGET}),
            patch.object(record, "_check_target_health", AsyncMock(return_value=healthy)),
            patch.object(record, "_self_ips", return_value=[]),
        ):
            req = _req(path="/health")
            resp = await record.handle_health(req)
            data = record.json.loads(resp.body)
            assert data["self_urls"] == []


# ---------------------------------------------------------------------------
# /ha/dashboards
# ---------------------------------------------------------------------------


class TestHandleHaDashboards:
    async def test_no_ha_target_returns_404(self):
        import record

        with (
            patch.object(record, "TARGETS", {"public": NONE_TARGET}),
            patch.object(record, "_ha_target", return_value=None),
        ):
            req = _req(path="/ha/dashboards")
            resp = await record.handle_ha_dashboards(req)
            assert resp.status == 404

    async def test_returns_dashboards(self):
        import record

        dashboards = [{"path": "/lovelace/0", "title": "Home"}]
        with (
            patch.object(record, "TARGETS", {"ha": HA_TARGET}),
            patch.object(record, "_ha_target", return_value=HA_TARGET),
            patch.object(record, "list_dashboards", AsyncMock(return_value=dashboards)),
        ):
            req = _req(path="/ha/dashboards")
            resp = await record.handle_ha_dashboards(req)
            assert resp.status == 200
            data = record.json.loads(resp.body)
            assert data["ok"]
            assert data["dashboards"] == dashboards

    async def test_list_dashboards_error_returns_502(self):
        import record

        with (
            patch.object(record, "TARGETS", {"ha": HA_TARGET}),
            patch.object(record, "_ha_target", return_value=HA_TARGET),
            patch.object(
                record, "list_dashboards", AsyncMock(side_effect=RuntimeError("ws error"))
            ),
        ):
            req = _req(path="/ha/dashboards")
            resp = await record.handle_ha_dashboards(req)
            assert resp.status == 502


# ---------------------------------------------------------------------------
# _ha_target
# ---------------------------------------------------------------------------


class TestHaTarget:
    def test_returns_target_named_ha(self):
        import record

        targets = {"ha": HA_TARGET, "public": NONE_TARGET}
        with patch.object(record, "TARGETS", targets):
            assert record._ha_target() == HA_TARGET

    def test_falls_back_to_first_ha_token_target(self):
        import record

        other_ha = {**HA_TARGET, "name": "myhome"}
        targets = {"myhome": other_ha, "public": NONE_TARGET}
        with patch.object(record, "TARGETS", targets):
            assert record._ha_target() == other_ha

    def test_returns_none_when_no_ha_token(self):
        import record

        targets = {"public": NONE_TARGET, "grafana": HEADER_TARGET}
        with patch.object(record, "TARGETS", targets):
            assert record._ha_target() is None


# ---------------------------------------------------------------------------
# Backward-compat config shim (logic proof — not record.py integration tests)
# NOTE: These tests verify the shim algorithm in isolation. They re-implement
# the logic inline by design — a reload-based approach would be needed to catch
# regressions in record.py's module-level shim code directly.
# ---------------------------------------------------------------------------


class TestConfigShim:
    def test_flat_token_becomes_ha_token_target(self):
        cfg = {"base_url": "http://ha:8123", "token": "mytoken"}
        if "token" in cfg and "auth" not in cfg:
            cfg["auth"] = {"strategy": "ha_token", "token": cfg["token"]}
        cfg["targets"] = [{"name": "default", "base_url": cfg["base_url"], "auth": cfg["auth"]}]
        targets = {t["name"]: t for t in cfg["targets"]}
        assert targets["default"]["auth"]["strategy"] == "ha_token"
        assert targets["default"]["auth"]["token"] == "mytoken"

    def test_flat_base_url_only_defaults_to_ha_token(self):
        cfg = {"base_url": "http://ha:8123"}
        if "base_url" in cfg and "targets" not in cfg:
            cfg["targets"] = [
                {
                    "name": "default",
                    "base_url": cfg["base_url"],
                    "auth": cfg.get("auth", {"strategy": "ha_token"}),
                }
            ]
        targets = {t["name"]: t for t in cfg["targets"]}
        assert targets["default"]["auth"]["strategy"] == "ha_token"

    def test_load_config_with_targets_json(self, tmp_path):
        import json

        import record

        cfg = tmp_path / "options.json"
        targets = [
            {
                "name": "ha",
                "base_url": "http://ha:8123",
                "auth": {"strategy": "ha_token", "token": "t"},
            }
        ]
        cfg.write_text(json.dumps({"targets_json": json.dumps(targets)}))
        with patch.dict("os.environ", {"CONFIG_PATH": str(cfg)}):
            record._load_config()
        assert "ha" in record.TARGETS

    def test_load_config_invalid_targets_json_raises(self, tmp_path):
        import json

        import record

        cfg = tmp_path / "options.json"
        cfg.write_text(json.dumps({"targets_json": "not-json"}))
        with patch.dict("os.environ", {"CONFIG_PATH": str(cfg)}):
            with pytest.raises(SystemExit):
                record._load_config()


# ---------------------------------------------------------------------------
# _check_target_health
# ---------------------------------------------------------------------------


def _mock_session(status=200, json_data=None, content_type="application/json", exception=None):
    """Build a mock aiohttp.ClientSession with distinct get/head context managers."""
    from unittest.mock import AsyncMock, MagicMock

    def _cm(exc=None):
        resp = MagicMock()
        resp.status = status
        resp.content_type = content_type
        resp.json = AsyncMock(return_value=json_data or {})
        cm = MagicMock()
        if exc:
            cm.__aenter__ = AsyncMock(side_effect=exc)
        else:
            cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    session = MagicMock()
    session.get = MagicMock(return_value=_cm(exception))
    session.head = MagicMock(return_value=_cm(exception))
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestCheckTargetHealth:
    async def test_empty_base_url_returns_ok(self):
        import record

        result = await record._check_target_health(
            {"name": "public", "base_url": "", "auth": {"strategy": "none"}}
        )
        assert result["ok"] is True
        assert result["probed"] is False

    async def test_ha_token_healthy(self):
        import record

        session = _mock_session(status=200, json_data={"message": "API running."})
        with patch("aiohttp.ClientSession", return_value=session):
            result = await record._check_target_health(HA_TARGET)
        assert result["ok"] is True
        assert result["strategy"] == "ha_token"
        assert result["probed"] is True
        assert result["detail"] == "API running."
        session.get.assert_called_once()
        session.head.assert_not_called()

    async def test_ha_token_bad_token(self):
        import record

        session = _mock_session(status=401)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await record._check_target_health(HA_TARGET)
        assert result["ok"] is False
        assert result["error"] == "bad token"

    async def test_ha_token_other_http_error(self):
        import record

        session = _mock_session(status=503)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await record._check_target_health(HA_TARGET)
        assert result["ok"] is False
        assert "503" in result["error"]

    async def test_none_strategy_healthy(self):
        import record

        session = _mock_session(status=200)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await record._check_target_health(NONE_TARGET)
        assert result["ok"] is True
        assert result["probed"] is True
        assert "200" in result["detail"]
        session.head.assert_called_once()
        session.get.assert_not_called()

    async def test_none_strategy_unhealthy(self):
        import record

        session = _mock_session(status=503)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await record._check_target_health(NONE_TARGET)
        assert result["ok"] is False

    async def test_connection_error_returns_ok_false(self):
        import aiohttp

        import record

        session = _mock_session(exception=aiohttp.ClientConnectionError("refused"))
        with patch("aiohttp.ClientSession", return_value=session):
            result = await record._check_target_health(NONE_TARGET)
        assert result["ok"] is False
        assert result["error"] == "unreachable"


# ---------------------------------------------------------------------------
# handle_config_ui
# ---------------------------------------------------------------------------


class TestHandleConfigUi:
    @pytest.mark.asyncio
    async def test_returns_html(self):
        import record

        req = make_mocked_request("GET", "/")
        resp = await record.handle_config_ui(req)
        assert resp.content_type == "text/html"
        assert b"DashSnap" in resp.body


# ---------------------------------------------------------------------------
# handle_config_get
# ---------------------------------------------------------------------------


class TestHandleConfigGet:
    @pytest.mark.asyncio
    async def test_returns_options_from_file(self, tmp_path):
        import json

        import record

        cfg = tmp_path / "options.json"
        targets = [
            {
                "name": "ha",
                "base_url": "http://ha.local:8123",
                "auth": {"strategy": "ha_token", "token": "tok"},
            }
        ]
        cfg.write_text(json.dumps({"targets": targets}))
        req = make_mocked_request("GET", "/config")
        with patch.dict("os.environ", {"CONFIG_PATH": str(cfg)}):
            resp = await record.handle_config_get(req)
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert "base_url" not in data
        assert "token" not in data
        assert "targets_json" not in data
        assert isinstance(data["targets"], list)
        ha = next(t for t in data["targets"] if t["name"] == "ha")
        assert ha["auth"]["token"] == "***"

    @pytest.mark.asyncio
    async def test_empty_token_stays_empty(self):
        import json

        import record

        targets = [
            {
                "name": "ha",
                "base_url": "http://ha.local:8123",
                "auth": {"strategy": "ha_token", "token": ""},
            }
        ]
        req = make_mocked_request("GET", "/config")
        with patch.object(record, "CFG", {"targets": targets}):
            resp = await record.handle_config_get(req)
        data = json.loads(resp.body)
        ha = next(t for t in data["targets"] if t["name"] == "ha")
        assert ha["auth"]["token"] == ""

    @pytest.mark.asyncio
    async def test_missing_file_returns_empty(self, tmp_path):
        import json

        import record

        req = make_mocked_request("GET", "/config")
        with patch.dict("os.environ", {"CONFIG_PATH": str(tmp_path / "missing.json")}):
            resp = await record.handle_config_get(req)
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert isinstance(data["targets"], list)
        assert any(t["name"] == "public" for t in data["targets"])

    @pytest.mark.asyncio
    async def test_invalid_json_file_returns_empty(self, tmp_path):
        import json

        import record

        cfg = tmp_path / "options.json"
        cfg.write_text("not-valid-json{{{")
        req = make_mocked_request("GET", "/config")
        with patch.dict("os.environ", {"CONFIG_PATH": str(cfg)}):
            resp = await record.handle_config_get(req)
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert isinstance(data["targets"], list)

    async def test_targets_array_returned_as_native_list(self, tmp_path):
        import json

        import record

        targets = [
            {"name": "ha", "base_url": "http://ha.local:8123", "auth": {"strategy": "ha_token"}}
        ]
        cfg = tmp_path / "options.json"
        cfg.write_text(json.dumps({"targets": targets}))
        req = make_mocked_request("GET", "/config")
        with patch.dict("os.environ", {"CONFIG_PATH": str(cfg)}):
            resp = await record.handle_config_get(req)
        data = json.loads(resp.body)
        assert isinstance(data["targets"], list)
        names = [t["name"] for t in data["targets"]]
        assert "ha" in names
        assert "public" in names

    async def test_public_target_always_first(self, tmp_path):
        import json

        import record

        cfg = tmp_path / "options.json"
        cfg.write_text(json.dumps({}))
        req = make_mocked_request("GET", "/config")
        with patch.dict("os.environ", {"CONFIG_PATH": str(cfg)}):
            resp = await record.handle_config_get(req)
        data = json.loads(resp.body)
        assert data["targets"][0]["name"] == "public"

    async def test_token_masked_in_targets(self, tmp_path):
        import json

        import record

        targets = [
            {
                "name": "ha",
                "base_url": "http://ha.local",
                "auth": {"strategy": "ha_token", "token": "secret"},
            }
        ]
        cfg = tmp_path / "options.json"
        cfg.write_text(json.dumps({"targets": targets}))
        req = make_mocked_request("GET", "/config")
        with patch.dict("os.environ", {"CONFIG_PATH": str(cfg)}):
            resp = await record.handle_config_get(req)
        data = json.loads(resp.body)
        ha = next(t for t in data["targets"] if t["name"] == "ha")
        assert ha["auth"]["token"] == "***"

    async def test_headers_masked_in_targets(self):
        import json

        import record

        targets = [
            {
                "name": "grafana",
                "auth": {"strategy": "http_header", "headers": {"Authorization": "Bearer secret"}},
            }
        ]
        req = make_mocked_request("GET", "/config")
        with patch.object(record, "CFG", {"targets": targets}):
            resp = await record.handle_config_get(req)
        data = json.loads(resp.body)
        grafana = next(t for t in data["targets"] if t["name"] == "grafana")
        assert grafana["auth"]["headers"]["Authorization"] == "***"

    async def test_invalid_targets_json_still_includes_public(self, tmp_path):
        import json

        import record

        cfg = tmp_path / "options.json"
        cfg.write_text(json.dumps({"targets_json": "not-valid-json"}))
        req = make_mocked_request("GET", "/config")
        with patch.dict("os.environ", {"CONFIG_PATH": str(cfg)}):
            resp = await record.handle_config_get(req)
        data = json.loads(resp.body)
        assert any(t["name"] == "public" for t in data["targets"])


# ---------------------------------------------------------------------------
# handle_config_save
# ---------------------------------------------------------------------------


class TestHandleConfigSave:
    @pytest.mark.asyncio
    async def test_save_strips_public_from_targets_json(self, tmp_path):
        import json

        import record

        cfg = tmp_path / "options.json"
        cfg.write_text("{}")
        targets_with_public = json.dumps(
            [
                {"name": "public", "auth": {"strategy": "none"}},
                {"name": "ha", "base_url": "http://ha.local", "auth": {"strategy": "ha_token"}},
            ]
        )
        req = make_mocked_request("POST", "/config")
        req.read = AsyncMock(
            return_value=json.dumps({"targets_json": targets_with_public}).encode()
        )
        with patch.dict("os.environ", {"SHADOW_CONFIG_PATH": str(cfg)}, clear=True):
            resp = await record.handle_config_save(req)
        assert resp.status == 200
        saved = json.loads(cfg.read_text())
        saved_targets = json.loads(saved["targets_json"])
        assert not any(t["name"] == "public" for t in saved_targets)

    async def test_no_supervisor_token_writes_file(self, tmp_path):
        import json

        import record

        cfg = tmp_path / "options.json"
        cfg.write_text(json.dumps({"base_url": "", "token": "", "targets_json": ""}))
        req = make_mocked_request("POST", "/config")
        req.read = AsyncMock(
            return_value=b'{"base_url":"http://ha.local:8123","token":"t","targets_json":""}'
        )
        with patch.dict("os.environ", {"SHADOW_CONFIG_PATH": str(cfg)}, clear=True):
            resp = await record.handle_config_save(req)
        assert resp.status == 200
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert data["restarting"] is False
        saved = json.loads(cfg.read_text())
        assert saved["base_url"] == "http://ha.local:8123"

    @pytest.mark.asyncio
    async def test_no_supervisor_token_file_error_returns_500(self, tmp_path):
        import json

        import record

        req = make_mocked_request("POST", "/config")
        req.read = AsyncMock(
            return_value=b'{"base_url":"http://ha.local:8123","token":"","targets_json":""}'
        )
        with patch.dict(
            "os.environ", {"SHADOW_CONFIG_PATH": "/nonexistent/path/options.json"}, clear=True
        ):
            resp = await record.handle_config_save(req)
        assert resp.status == 500
        data = json.loads(resp.body)
        assert data["ok"] is False

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self):
        import json

        import record

        req = make_mocked_request("POST", "/config")
        req.read = AsyncMock(return_value=b"not-json")
        with patch.dict("os.environ", {"SUPERVISOR_TOKEN": "sup-tok"}):
            resp = await record.handle_config_save(req)
        assert resp.status == 400
        data = json.loads(resp.body)
        assert data["ok"] is False

    @pytest.mark.asyncio
    async def test_invalid_targets_json_returns_400(self):
        import json

        import record

        req = make_mocked_request("POST", "/config")
        req.read = AsyncMock(
            return_value=b'{"base_url":"","token":"","targets_json":"not-valid-json"}'
        )
        with patch.dict("os.environ", {"SUPERVISOR_TOKEN": "sup-tok"}):
            resp = await record.handle_config_save(req)
        assert resp.status == 400
        data = json.loads(resp.body)
        assert data["ok"] is False
        assert "targets_json" in data["error"]

    @pytest.mark.asyncio
    async def test_masked_token_not_forwarded(self, tmp_path):
        import json

        import record

        cfg_path = tmp_path / "options.json"
        cfg_path.write_text("{}")
        req = make_mocked_request("POST", "/config")
        req.read = AsyncMock(
            return_value=b'{"base_url":"http://ha.local:8123","token":"***","targets_json":""}'
        )
        captured = {}
        ok_resp = AsyncMock()
        ok_resp.status = 200
        ok_resp.__aenter__ = AsyncMock(return_value=ok_resp)
        ok_resp.__aexit__ = AsyncMock(return_value=False)

        import aiohttp

        drop_cm = AsyncMock()
        drop_cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientConnectionError("dropped"))
        drop_cm.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        def _post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                captured.update(kwargs.get("json", {}))
                return ok_resp
            return drop_cm

        session = AsyncMock()
        session.post = _post
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.dict(
            "os.environ", {"SUPERVISOR_TOKEN": "sup-tok", "SHADOW_CONFIG_PATH": str(cfg_path)}
        ):
            with patch("aiohttp.ClientSession", return_value=session):
                resp = await record.handle_config_save(req)
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert "token" not in captured.get("options", {})

    @pytest.mark.asyncio
    async def test_config_written_before_restart_survives_supervisor_wipe(self, tmp_path):
        """Config written to options.json before restart — survives supervisor wipe."""
        import json

        import aiohttp

        import record

        cfg_path = tmp_path / "options.json"
        cfg_path.write_text('{"targets": []}')

        req = make_mocked_request("POST", "/config")
        req.read = AsyncMock(
            return_value=b'{"base_url":"http://ha.local:8123","token":"t","targets_json":"[]"}'
        )
        ok_resp = AsyncMock()
        ok_resp.status = 200
        ok_resp.__aenter__ = AsyncMock(return_value=ok_resp)
        ok_resp.__aexit__ = AsyncMock(return_value=False)

        drop_cm = AsyncMock()
        drop_cm.__aenter__ = AsyncMock(side_effect=aiohttp.ServerDisconnectedError())
        drop_cm.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        def _post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return ok_resp if call_count == 1 else drop_cm

        session = AsyncMock()
        session.post = _post
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.dict(
            "os.environ", {"SUPERVISOR_TOKEN": "sup-tok", "SHADOW_CONFIG_PATH": str(cfg_path)}
        ):
            with patch("aiohttp.ClientSession", return_value=session):
                resp = await record.handle_config_save(req)
        assert resp.status == 200
        saved = json.loads(cfg_path.read_text())
        assert saved["base_url"] == "http://ha.local:8123"

    @pytest.mark.asyncio
    async def test_supervisor_error_falls_back_to_direct_write(self, tmp_path):
        import json

        import record

        cfg_path = tmp_path / "options.json"
        cfg_path.write_text("{}")

        req = make_mocked_request("POST", "/config")
        req.read = AsyncMock(return_value=b'{"base_url":"","token":"","targets_json":"[]"}')
        mock_opts_resp = AsyncMock()
        mock_opts_resp.status = 400
        mock_opts_resp.__aenter__ = AsyncMock(return_value=mock_opts_resp)
        mock_opts_resp.__aexit__ = AsyncMock(return_value=False)

        mock_restart_resp = AsyncMock()
        mock_restart_resp.status = 200
        mock_restart_resp.__aenter__ = AsyncMock(return_value=mock_restart_resp)
        mock_restart_resp.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        def _post(*a, **kw):
            nonlocal call_count
            call_count += 1
            return mock_opts_resp if call_count == 1 else mock_restart_resp

        session = AsyncMock()
        session.post = _post
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.dict(
                "os.environ", {"SUPERVISOR_TOKEN": "sup-tok", "SHADOW_CONFIG_PATH": str(cfg_path)}
            ),
            patch("aiohttp.ClientSession", return_value=session),
        ):
            resp = await record.handle_config_save(req)
        assert resp.status == 200
        data = json.loads(resp.body)
        assert data["ok"] is True

    @pytest.mark.asyncio
    async def test_supervisor_error_fallback_missing_json(self, tmp_path):
        """Supervisor rejects options — fallback reads invalid options.json (starts fresh)."""
        import record

        cfg_path = tmp_path / "options.json"
        cfg_path.write_text("not json")

        req = make_mocked_request("POST", "/config")
        req.read = AsyncMock(return_value=b'{"base_url":"","token":"","targets_json":"[]"}')
        mock_opts_resp = AsyncMock()
        mock_opts_resp.status = 422
        mock_opts_resp.__aenter__ = AsyncMock(return_value=mock_opts_resp)
        mock_opts_resp.__aexit__ = AsyncMock(return_value=False)
        mock_restart_resp = AsyncMock()
        mock_restart_resp.status = 200
        mock_restart_resp.__aenter__ = AsyncMock(return_value=mock_restart_resp)
        mock_restart_resp.__aexit__ = AsyncMock(return_value=False)
        call_count = 0

        def _post(*a, **kw):
            nonlocal call_count
            call_count += 1
            return mock_opts_resp if call_count == 1 else mock_restart_resp

        session = AsyncMock()
        session.post = _post
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.dict(
                "os.environ", {"SUPERVISOR_TOKEN": "sup-tok", "SHADOW_CONFIG_PATH": str(cfg_path)}
            ),
            patch("aiohttp.ClientSession", return_value=session),
        ):
            resp = await record.handle_config_save(req)
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_supervisor_error_fallback_write_fails(self):
        """Supervisor rejects options — direct write fails → returns 500."""
        import json

        import record

        req = make_mocked_request("POST", "/config")
        req.read = AsyncMock(return_value=b'{"base_url":"","token":"","targets_json":"[]"}')
        mock_opts_resp = AsyncMock()
        mock_opts_resp.status = 422
        mock_opts_resp.__aenter__ = AsyncMock(return_value=mock_opts_resp)
        mock_opts_resp.__aexit__ = AsyncMock(return_value=False)
        session = AsyncMock()
        session.post = lambda *a, **kw: mock_opts_resp
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.dict(
                "os.environ",
                {
                    "SUPERVISOR_TOKEN": "sup-tok",
                    "SHADOW_CONFIG_PATH": "/nonexistent/path/options.json",
                },
            ),
            patch("aiohttp.ClientSession", return_value=session),
        ):
            resp = await record.handle_config_save(req)
        assert resp.status == 500
        data = json.loads(resp.body)
        assert data["ok"] is False

    @pytest.mark.asyncio
    async def test_success_with_connection_drop(self, tmp_path):
        import json

        import aiohttp

        import record

        cfg_path = tmp_path / "options.json"
        cfg_path.write_text("{}")
        req = make_mocked_request("POST", "/config")
        req.read = AsyncMock(
            return_value=b'{"base_url":"http://ha.local:8123","token":"t","targets_json":""}'
        )
        ok_resp = AsyncMock()
        ok_resp.status = 200
        ok_resp.__aenter__ = AsyncMock(return_value=ok_resp)
        ok_resp.__aexit__ = AsyncMock(return_value=False)

        drop_cm = AsyncMock()
        drop_cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientConnectionError("dropped"))
        drop_cm.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        def _post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return ok_resp if call_count == 1 else drop_cm

        session = AsyncMock()
        session.post = _post
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.dict(
            "os.environ", {"SUPERVISOR_TOKEN": "sup-tok", "SHADOW_CONFIG_PATH": str(cfg_path)}
        ):
            with patch("aiohttp.ClientSession", return_value=session):
                resp = await record.handle_config_save(req)
        data = json.loads(resp.body)
        assert data["ok"] is True

    @pytest.mark.asyncio
    async def test_success_with_server_disconnected(self, tmp_path):
        import json

        import aiohttp

        import record

        cfg_path = tmp_path / "options.json"
        cfg_path.write_text("{}")
        req = make_mocked_request("POST", "/config")
        req.read = AsyncMock(
            return_value=b'{"base_url":"http://ha.local:8123","token":"t","targets_json":""}'
        )
        ok_resp = AsyncMock()
        ok_resp.status = 200
        ok_resp.__aenter__ = AsyncMock(return_value=ok_resp)
        ok_resp.__aexit__ = AsyncMock(return_value=False)

        drop_cm = AsyncMock()
        drop_cm.__aenter__ = AsyncMock(side_effect=aiohttp.ServerDisconnectedError())
        drop_cm.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        def _post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return ok_resp if call_count == 1 else drop_cm

        session = AsyncMock()
        session.post = _post
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.dict(
            "os.environ", {"SUPERVISOR_TOKEN": "sup-tok", "SHADOW_CONFIG_PATH": str(cfg_path)}
        ):
            with patch("aiohttp.ClientSession", return_value=session):
                resp = await record.handle_config_save(req)
        data = json.loads(resp.body)
        assert data["ok"] is True

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_502(self, tmp_path):
        import json

        import record

        cfg_path = tmp_path / "options.json"
        cfg_path.write_text("{}")
        req = make_mocked_request("POST", "/config")
        req.read = AsyncMock(
            return_value=b'{"base_url":"http://ha.local:8123","token":"t","targets_json":""}'
        )

        def _boom(*a, **kw):
            raise RuntimeError("boom")

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.post = _boom
        with patch.dict(
            "os.environ", {"SUPERVISOR_TOKEN": "sup-tok", "SHADOW_CONFIG_PATH": str(cfg_path)}
        ):
            with patch("aiohttp.ClientSession", return_value=session):
                resp = await record.handle_config_save(req)
        assert resp.status == 502
        data = json.loads(resp.body)
        assert data["ok"] is False
        assert "boom" in data["error"]
