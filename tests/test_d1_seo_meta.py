"""D-1：三个站点页与作品详情页补 meta description + Open Graph。

此前 templates/ 下 og 标签零命中，链接分享出去只有裸 URL。这里把四页的
description / og:title / og:description / og:type / og:url / og:image 钉住；
og:image 走仓库既有资产链路：作品页复用 termify/gallery.py:197 make_og_image
在上传时烘好的 1200x630 图（由 /gallery/file/<id>/og 提供），站点页用
static/img 下现成的风格预览图。
"""
import json

import pytest


def _client():
    from app import app as flask_app
    return flask_app.test_client()


@pytest.fixture
def isolated_gallery(monkeypatch, tmp_path):
    """把画廊库指到 tmp_path，避免测试读写仓库里的 data/（已 gitignore 的本地数据）。"""
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_d1.db"))

    from termify.taskstore import cache_clear_all, reset_store_for_tests

    cache_clear_all()
    reset_store_for_tests()
    import app as app_mod
    from termify import gallery as gallery_mod

    gdata = tmp_path / "gallery_data"
    gdata.mkdir()
    monkeypatch.setattr(app_mod, "GALLERY_DATA_DIR", str(gdata))
    db = gallery_mod.GalleryDB(str(gdata / "termify.db"))
    db.init_db()
    monkeypatch.setattr(app_mod, "GALLERY_DB", db)
    app_mod._RL_LOG.clear()
    yield db
    cache_clear_all()
    reset_store_for_tests()


def _work(work_id, title, author, description=""):
    return {
        "id": work_id,
        "title": title,
        "description": description,
        "tags": json.dumps([]),
        "author": author,
        "source_path": "/nonexistent/source.png",
        "thumbnail_path": "/nonexistent/thumb.png",
        "og_path": "/nonexistent/og.png",
        "params_json": json.dumps({"kind": "image"}),
        "is_private": 0,
        "admin_token": "tok-" + work_id,
        "created_at": "2026-01-02T03:04:05",
        "ip": "203.0.113.10",
    }


def _meta(html, attr, value):
    """抓出 <meta {attr}="{value}" content="..."> 的 content。"""
    import re as _re
    m = _re.search(r'<meta\s+%s="%s"\s+content="([^"]*)"' % (attr, _re.escape(value)), html)
    assert m, '缺少 <meta %s="%s">' % (attr, value)
    return m.group(1)


@pytest.mark.parametrize("path", ["/", "/text-art", "/gallery"])
def test_site_pages_have_description_and_og(path):
    html = _client().get(path).get_data(as_text=True)
    desc = _meta(html, "name", "description").strip()
    assert desc, "description 不能为空"
    assert len(desc) <= 160, "description 超过 160 字符：%d" % len(desc)
    for prop in ("og:title", "og:description", "og:type", "og:url", "og:image"):
        assert _meta(html, "property", prop).strip(), prop
    assert _meta(html, "property", "og:type") == "website"
    assert _meta(html, "property", "og:url").endswith(path)
    assert _meta(html, "property", "og:image").startswith("http")
    assert _meta(html, "property", "og:description") == desc


def test_og_image_reuses_existing_static_asset_on_site_pages():
    """站点页没有 work_id，只能指向仓库里现成的图片资产（不许另造一套生成逻辑）。"""
    html = _client().get("/").get_data(as_text=True)
    img = _meta(html, "property", "og:image")
    assert "/static/img/" in img
    assert img.endswith(".gif") or img.endswith(".png")
    # 该资产必须真的在磁盘上
    from pathlib import Path
    rel = img.split("/static/", 1)[1]
    assert (Path(__file__).resolve().parents[1] / "static" / rel).is_file(), rel


def test_work_page_og_uses_real_title_author_and_gallery_og_asset(isolated_gallery):
    isolated_gallery.insert_work(_work("d1work0001", "赛博朋克猫", "张三"))
    html = _client().get("/v/d1work0001").get_data(as_text=True)
    assert _meta(html, "property", "og:title").startswith("赛博朋克猫")
    assert "张三" in _meta(html, "property", "og:description")
    assert _meta(html, "property", "og:type") == "article"
    assert _meta(html, "property", "og:url").endswith("/v/d1work0001")
    # 复用上传时烘好的 OG 图（gallery_og 路由），不是另造的东西
    assert "/gallery/file/d1work0001/og" in _meta(html, "property", "og:image")
    desc = _meta(html, "name", "description")
    assert desc and len(desc) <= 160


def test_work_page_description_falls_back_to_title_and_author(isolated_gallery):
    isolated_gallery.insert_work(_work("d1work0002", "无描述作品", "李四"))
    html = _client().get("/v/d1work0002").get_data(as_text=True)
    desc = _meta(html, "name", "description")
    assert "无描述作品" in desc and "李四" in desc
    assert len(desc) <= 160


def test_seo_block_adds_no_inline_script():
    """CSP 是 script-src 'self'：这段只允许写 <meta>。"""
    from pathlib import Path
    block = (Path(__file__).resolve().parents[1] / "templates" / "_seo_meta.html").read_text(encoding="utf-8")
    body = "\n".join(ln for ln in block.splitlines() if not ln.strip().startswith("{#") and not ln.strip().startswith("#"))
    assert "<script" not in body
    assert "onload=" not in body and "onerror=" not in body

def test_og_url_follows_x_forwarded_proto_but_rejects_garbage():
    """线上在 Caddy 反代后面，Flask 只看到 http；og:url 要跟 X-Forwarded-Proto 走。
    但该头是客户端可写的，除 http/https 之外一律回落到真实 scheme。"""
    c = _client()
    https_html = c.get("/gallery", headers={"X-Forwarded-Proto": "https"}).get_data(as_text=True)
    assert _meta(https_html, "property", "og:url").startswith("https://")
    assert _meta(https_html, "property", "og:image").startswith("https://")
    junk_html = c.get("/gallery", headers={"X-Forwarded-Proto": "file"}).get_data(as_text=True)
    assert _meta(junk_html, "property", "og:url").startswith("http://")
