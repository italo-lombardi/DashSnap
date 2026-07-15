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


class TestHandleHealth:
    async def test_no_targets_returns_503(self):
        import record

        with patch.object(record, "TARGETS", {}):
            req = _req(path="/health")
            resp = await record.handle_health(req)
            assert resp.status == 503

    async def test_all_healthy_returns_200(self):
        import record

        healthy = {"name": "ha", "ok": True, "strategy": "ha_token", "base_url": "http://ha:8123"}
        with (
            patch.object(record, "TARGETS", {"ha": HA_TARGET}),
            patch.object(record, "_check_target_health", AsyncMock(return_value=healthy)),
        ):
            req = _req(path="/health")
            resp = await record.handle_health(req)
            assert resp.status == 200
            data = record.json.loads(resp.body)
            assert data["ok"]

    async def test_unhealthy_target_returns_502(self):
        import record

        unhealthy = {
            "name": "ha",
            "ok": False,
            "strategy": "ha_token",
            "base_url": "http://ha:8123",
            "error": "refused",
        }
        with (
            patch.object(record, "TARGETS", {"ha": HA_TARGET}),
            patch.object(record, "_check_target_health", AsyncMock(return_value=unhealthy)),
        ):
            req = _req(path="/health")
            resp = await record.handle_health(req)
            assert resp.status == 502
            data = record.json.loads(resp.body)
            assert not data["ok"]


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
    async def test_ha_token_healthy(self):
        import record

        session = _mock_session(status=200, json_data={"message": "API running."})
        with patch("aiohttp.ClientSession", return_value=session):
            result = await record._check_target_health(HA_TARGET)
        assert result["ok"] is True
        assert result["strategy"] == "ha_token"
        assert result["ha"] == "API running."
        session.get.assert_called_once()
        session.head.assert_not_called()

    async def test_ha_token_bad_token(self):
        import record

        session = _mock_session(status=401)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await record._check_target_health(HA_TARGET)
        assert result["ok"] is False
        assert result["hint"] == "bad token"

    async def test_ha_token_other_http_error(self):
        import record

        session = _mock_session(status=503)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await record._check_target_health(HA_TARGET)
        assert result["ok"] is False
        assert "503" in result["hint"]

    async def test_none_strategy_healthy(self):
        import record

        session = _mock_session(status=200)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await record._check_target_health(NONE_TARGET)
        assert result["ok"] is True
        assert result["http_status"] == 200
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
        assert "refused" in result["error"]


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
        cfg.write_text(
            json.dumps({"base_url": "http://ha.local:8123", "token": "tok", "targets_json": ""})
        )
        req = make_mocked_request("GET", "/config")
        with patch.dict("os.environ", {"CONFIG_PATH": str(cfg)}):
            resp = await record.handle_config_get(req)
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert data["base_url"] == "http://ha.local:8123"
        assert data["token"] == "***"

    @pytest.mark.asyncio
    async def test_empty_token_returns_empty(self, tmp_path):
        import json

        import record

        cfg = tmp_path / "options.json"
        cfg.write_text(
            json.dumps({"base_url": "http://ha.local:8123", "token": "", "targets_json": ""})
        )
        req = make_mocked_request("GET", "/config")
        with patch.dict("os.environ", {"CONFIG_PATH": str(cfg)}):
            resp = await record.handle_config_get(req)
        data = json.loads(resp.body)
        assert data["token"] == ""

    @pytest.mark.asyncio
    async def test_missing_file_returns_empty(self, tmp_path):
        import json

        import record

        req = make_mocked_request("GET", "/config")
        with patch.dict("os.environ", {"CONFIG_PATH": str(tmp_path / "missing.json")}):
            resp = await record.handle_config_get(req)
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert data["base_url"] == ""

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
        assert data["base_url"] == ""


# ---------------------------------------------------------------------------
# handle_config_save
# ---------------------------------------------------------------------------


class TestHandleConfigSave:
    @pytest.mark.asyncio
    async def test_no_supervisor_token_writes_file(self, tmp_path):
        import json

        import record

        cfg = tmp_path / "options.json"
        cfg.write_text(json.dumps({"base_url": "", "token": "", "targets_json": ""}))
        req = make_mocked_request("POST", "/config")
        req.read = AsyncMock(
            return_value=b'{"base_url":"http://ha.local:8123","token":"t","targets_json":""}'
        )
        with patch.dict("os.environ", {"CONFIG_PATH": str(cfg)}, clear=True):
            resp = await record.handle_config_save(req)
        assert resp.status == 200
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert "restart_required" not in data
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
            "os.environ", {"CONFIG_PATH": "/nonexistent/path/options.json"}, clear=True
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
    async def test_masked_token_not_forwarded(self):
        import json

        import record

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
        with patch.dict("os.environ", {"SUPERVISOR_TOKEN": "sup-tok"}):
            with patch("aiohttp.ClientSession", return_value=session):
                resp = await record.handle_config_save(req)
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert "token" not in captured.get("options", {})

    @pytest.mark.asyncio
    async def test_supervisor_error_returns_502(self):
        import json

        import record

        req = make_mocked_request("POST", "/config")
        req.read = AsyncMock(
            return_value=b'{"base_url":"http://ha.local:8123","token":"t","targets_json":""}'
        )
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text = AsyncMock(return_value="bad request")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        session = AsyncMock()
        session.post = lambda *a, **kw: mock_resp
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.dict("os.environ", {"SUPERVISOR_TOKEN": "sup-tok"}):
            with patch("aiohttp.ClientSession", return_value=session):
                resp = await record.handle_config_save(req)
        assert resp.status == 502
        data = json.loads(resp.body)
        assert data["ok"] is False

    @pytest.mark.asyncio
    async def test_success_with_connection_drop(self):
        import json

        import aiohttp

        import record

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
        with patch.dict("os.environ", {"SUPERVISOR_TOKEN": "sup-tok"}):
            with patch("aiohttp.ClientSession", return_value=session):
                resp = await record.handle_config_save(req)
        data = json.loads(resp.body)
        assert data["ok"] is True

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_502(self):
        import json

        import record

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
        with patch.dict("os.environ", {"SUPERVISOR_TOKEN": "sup-tok"}):
            with patch("aiohttp.ClientSession", return_value=session):
                resp = await record.handle_config_save(req)
        assert resp.status == 502
        data = json.loads(resp.body)
        assert data["ok"] is False
        assert "boom" in data["error"]
