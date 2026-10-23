"""T51 请求体解析统一 + 重复斜杠/资源 404 一致性。

背景：13 处端点原先都写 `request.get_json(silent=True) or {}`，坏 JSON、错
Content-Type（表单/纯文本）全被当成空对象继续跑，最终报一个与真实原因无关的
"缺字段"，/api/cjk/ttf/fontwall 这类还干脆 200 返回整页字体墙。现在统一走
`_json_body()`：无 body 仍是 {}（老客户端不变），坏 JSON / 非 JSON
Content-Type / 非对象 body 一律 400 + application/json。

本文件锁四件事：
1. 坏 JSON → 400 JSON，且带 /api/* 既有的 no-store；
2. 错 Content-Type → 400 JSON；
3. 空 body / null / 纯空白的合法路径与改前完全一致（含"业务级 400"不被
   换成解析级 400）；
4. 合法 JSON 的响应体结构不变，以及解析顺序没有抢在鉴权/存在性检查之前。
"""

from __future__ import annotations

import json

import pytest

flask = pytest.importorskip("flask")

JSON = "application/json"
BAD_JSON = '{"text": '                 # 语法未闭合的对象
MALFORMED = "请求体 JSON 解析失败"
NEED_JSON = "请求体必须是 JSON"
NOT_OBJECT = "JSON body must be an object"


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    import app as app_mod
    app_mod._RL_LOG.clear()
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()


def _json(resp):
    return json.loads(resp.get_data().decode("utf-8"))


# --- 1. 坏 JSON → 400 JSON ---------------------------------------------------

@pytest.mark.parametrize("path", [
    "/api/text/convert",
    "/api/cjk/ttf/render",
    "/api/gallery/upload-text",
    "/api/text/terminal-command",
    "/api/text/export-ansi",
])
def test_bad_json_is_400_json(client, path):
    r = client.post(path, data=BAD_JSON, content_type=JSON)
    assert r.status_code == 400
    assert r.headers["Content-Type"].startswith("application/json")
    payload = _json(r)
    assert payload["status"] == 400
    assert MALFORMED in payload["error"]


def test_bad_json_keeps_api_no_store(client):
    """/api/* 的兜底 no-store 必须照样贴到新的 400 上（别让坏请求被缓存）。"""
    r = client.post("/api/text/convert", data=BAD_JSON, content_type=JSON)
    assert r.headers.getlist("Cache-Control") == ["no-store"]


def test_bad_json_no_longer_silently_succeeds(client):
    """改前 /api/cjk/ttf/fontwall 拿坏 JSON 会当空对象、200 回 81KB 字体墙。"""
    r = client.post("/api/cjk/ttf/fontwall", data=BAD_JSON, content_type=JSON)
    assert r.status_code == 400
    assert "charsets" not in _json(r)


# --- 2. 错 Content-Type → 400 JSON ------------------------------------------

@pytest.mark.parametrize("path,body,ct", [
    ("/api/text/convert", b"text=hi", "text/plain"),
    ("/api/cjk/ttf/render", b"text=hi", "text/plain"),
    ("/api/text/convert", b"text=hi", "application/x-www-form-urlencoded"),
    ("/api/gallery/upload-text", b"art=hi", "multipart/form-data; boundary=x"),
])
def test_wrong_content_type_is_400_json(client, path, body, ct):
    r = client.post(path, data=body, content_type=ct)
    assert r.status_code == 400
    assert r.headers["Content-Type"].startswith("application/json")
    assert NEED_JSON in _json(r)["error"]


# --- 3. 空 body 与改前逐项一致 ----------------------------------------------

def test_empty_body_still_succeeds_where_it_used_to(client):
    """无 body 的 /api/cjk/ttf/fontwall 改前 200 + 全部字符集，现在必须一样。"""
    no_body = client.post("/api/cjk/ttf/fontwall")
    empty_obj = client.post("/api/cjk/ttf/fontwall", data="{}", content_type=JSON)
    assert no_body.status_code == 200
    assert empty_obj.status_code == 200
    assert no_body.get_data() == empty_obj.get_data()
    assert _json(no_body)["charsets"]


@pytest.mark.parametrize("body", [None, b"", b"null", b"   "])
def test_bodyless_and_null_are_treated_as_empty_object(client, body):
    """无 body / null / 纯空白都等价于 {}，不会掉进 400。"""
    kw = {} if body is None else {"data": body, "content_type": JSON}
    r = client.post("/api/cjk/ttf/fontwall", **kw)
    assert r.status_code == 200
    assert _json(r)["charsets"]


def test_empty_body_keeps_business_level_400(client):
    """空 body 走到业务校验的端点仍报业务错误，不能被解析层抢成"必须是 JSON"。"""
    r = client.post("/api/text/convert")
    assert r.status_code == 400
    err = _json(r)["error"]
    assert NEED_JSON not in err and MALFORMED not in err
    assert "英文" in err


def test_json_body_helper_semantics_on_blank_body():
    """单元级：_json_body() 对无 body 直接给 {}，且不依赖端点。"""
    import app as app_mod
    with app_mod.app.test_request_context("/whatever", method="POST", data=b"  "):
        assert app_mod._json_body() == {}


# --- 4. 合法 JSON 行为不变 ---------------------------------------------------

def test_legal_json_convert_unchanged(client):
    r = client.post("/api/text/convert",
                    data=json.dumps({"text": "hi", "font": "ansi_shadow"}),
                    content_type=JSON)
    assert r.status_code == 200
    payload = _json(r)
    assert payload["art"].startswith("██╗")


def test_legal_json_cjk_render_unchanged(client):
    r = client.post("/api/cjk/ttf/render",
                    data=json.dumps({"text": "hi"}), content_type=JSON)
    assert r.status_code == 200
    assert _json(r)["art"].strip()


@pytest.mark.parametrize("path,body", [
    ("/api/text/convert", {"text": "hi", "font": "ansi_shadow"}),
    ("/api/text/terminal-command", {"art": "hi"}),
    ("/api/text/export-ansi", {"art": "hi"}),
])
def test_legal_json_has_no_error_key(client, path, body):
    r = client.post(path, data=json.dumps(body), content_type=JSON)
    assert r.status_code == 200
    assert "error" not in _json(r)


def test_non_object_json_is_rejected(client):
    """改前 /api/generate 收 [1,2] 直接 500（AttributeError: 'list' ...）。"""
    r = client.post("/api/generate", data="[1,2]", content_type=JSON)
    assert r.status_code == 400
    assert NOT_OBJECT in _json(r)["error"]


# --- 5. 解析顺序没被抢到鉴权/存在性检查前面 ---------------------------------

def test_report_parses_body_after_work_lookup(client):
    """作品不存在时先 404，不要因为 body 坏就改报 400。"""
    r = client.post("/api/gallery/report/nosuchwork",
                    data=BAD_JSON, content_type=JSON)
    assert r.status_code == 404
    assert _json(r)["error"] == "Work not found"


def test_admin_report_parses_body_after_admin_check(client):
    """未授权时先 403，不要因为 body 坏就改报 400。"""
    r = client.post("/api/gallery/admin/report/1",
                    data=BAD_JSON, content_type=JSON)
    assert r.status_code == 403
    assert _json(r)["error"] == "Unauthorized"


# --- 6. 重复斜杠一次跳到位 ---------------------------------------------------

@pytest.mark.parametrize("path,target", [
    ("/text-art//", "/text-art"),
    ("/gallery//", "/gallery"),
    ("/gallery///", "/gallery"),
])
def test_duplicate_slash_is_one_hop(client, path, target):
    """改前是 308(/text-art/) → 301(/text-art) 两跳；现在一跳 301。"""
    r = client.get(path)
    assert r.status_code == 301
    assert r.headers["Location"] == target
    h = client.head(path)
    assert h.status_code == 301
    assert h.headers["Location"] == target


@pytest.mark.parametrize("path,target", [
    ("/text-art//", "/text-art"),
    ("/gallery//", "/gallery"),
])
def test_duplicate_slash_follows_to_200_in_one_redirect(client, path, target):
    r = client.get(path, follow_redirects=True)
    assert r.status_code == 200
    assert len(r.history) == 1
    assert r.request.path == target


def test_duplicate_slash_keeps_query_string(client):
    r = client.get("/gallery//?page=2")
    assert r.status_code == 301
    assert r.headers["Location"] == "/gallery?page=2"


@pytest.mark.parametrize("path,target", [
    ("/text-art/", "/text-art"),
    ("/gallery/", "/gallery"),
])
def test_single_trailing_slash_still_301(client, path, target):
    """单尾斜杠的既有 301 策略不受影响（T49 已锁，这里防回归到新逻辑）。"""
    r = client.get(path)
    assert r.status_code == 301
    assert r.headers["Location"] == target


def test_api_prefix_is_not_slash_rewritten(client):
    """/api/* 的斜杠归一归 API 契约管，新逻辑不碰。"""
    r = client.post("/api//text/convert", data="{}", content_type=JSON)
    assert r.status_code != 301
    assert r.headers.get("Location") in (None, "http://localhost/api/text/convert")


# --- 7. 资源直链 404 回 JSON -------------------------------------------------

@pytest.mark.parametrize("kind", ["source", "thumb", "og", "audio"])
def test_gallery_file_404_is_json(client, kind):
    """这些 URL 给 <img src>/播放器用：改前是 2.3KB 的 404.html，现在是 JSON。"""
    r = client.get("/gallery/file/nope/" + kind)
    assert r.status_code == 404
    assert r.headers["Content-Type"].startswith("application/json")
    assert r.headers.get("Cache-Control") == "no-store"
    payload = _json(r)
    assert payload["status"] == 404
    assert b"<html" not in r.get_data()


def test_api_404_json_unchanged(client):
    r = client.get("/api/gallery/work/nope")
    assert r.status_code == 404
    assert _json(r) == {"error": "Work not found"}


def test_page_404_still_uses_the_html_template(client):
    """页面级 404 仍走 404.html —— 资源直链的 JSON 化没有波及整站。"""
    r = client.get("/definitely-not-a-page-t51")
    assert r.status_code == 404
    assert r.headers["Content-Type"].startswith("text/html")
    assert b"<html" in r.get_data()
