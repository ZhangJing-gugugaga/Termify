"""T34 — 页面重构：动画工坊归一 / 文字艺术独立页 / 画廊自定义标签。

覆盖：
- 页面 1：三段归一为「动画工坊」（studio-section），导航三项化，文字卡片移除
- 页面 2：/text-art 独立页渲染（导航/标题/工作台骨架）
- 自定义标签：发布路径（图片/文字）清洗并存取；计数端点；多标签筛选
- XSS 契约：自定义标签进 /v/ 页必须被 tojson/escapeHtml 中和
"""

from __future__ import annotations

import importlib.util
import io
import json

import pytest
from PIL import Image

pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("flask"),
    reason="flask 未安装",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t34.db"))
    monkeypatch.delenv("TERMIFY_ADMIN_PWD", raising=False)

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
    yield
    cache_clear_all()
    reset_store_for_tests()
    app_mod._RL_LOG.clear()


@pytest.fixture
def client():
    from app import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _png_body(color=(20, 200, 80), size=(32, 32)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _upload_image(client, *, title="T34", tags=None, custom=None, private="0"):
    return client.post(
        "/api/gallery/upload",
        data={"source": (io.BytesIO(_png_body()), "t34.png"),
              "title": title,
              "tags": json.dumps(tags or []),
              "custom_tags": json.dumps(custom or []),
              "is_private": private,
              "params": json.dumps({"charset": "ascii", "width": 40, "height": 20})},
        content_type="multipart/form-data",
    )


# ── 页面结构 ─────────────────────────────────────────────────

def test_index_merges_into_studio(client):
    page = client.get("/")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "studio-section" in body and 'id="studio"' in body
    assert "动画工坊" in body
    # 三个子块仍在同一 section 内
    for anchor in ('id="upload"', 'id="styles"', 'id="preview"'):
        assert anchor in body
    # 导航三项化
    assert 'href="/text-art"' in body and "文字艺术" in body
    assert 'href="#upload"' not in body and 'href="#styles"' not in body
    # 文字艺术卡片已移除，仅留入口
    assert 'id="textArtInput"' not in body
    assert "前往文字艺术" in body


def test_text_art_page_renders(client):
    page = client.get("/text-art")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "文字艺术" in body and "text_art.js" in body
    # 导航三项齐全
    assert 'href="/"' in body and 'href="/gallery"' in body
    # 工作台骨架
    assert 'id="taInput"' in body and 'id="taOutput"' in body
    # T37 单 Tab 无 LLM：只有「生成」按钮，AI 按钮/设置面板全部移除
    assert 'id="taConvertBtn"' in body
    assert 'id="taAiBtn"' not in body
    assert 'id="taSettingsBtn"' not in body
    assert 'id="taSettings"' not in body
    assert 'id="taCjkBtn"' not in body
    assert 'taSettingsBtn' not in body  # HTML 内任何形式都不留
    # 共享发布弹窗（含自定义标签输入）
    assert 'id="galleryCustomTags"' in body and 'id="customTagsCount"' in body


def test_gallery_nav_three_items(client):
    page = client.get("/gallery")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert 'href="/text-art"' in body
    assert 'href="/#upload"' not in body
    # 预设标签筛选行移除，自定义下拉存在
    assert "tag-chip" not in body
    assert 'id="customTagsBtn"' in body


# ── 自定义标签：清洗 + 存取 ─────────────────────────────────

def test_sanitize_custom_tags_rules():
    from termify import gallery as g

    out = g.sanitize_custom_tags(
        ["赛博朋克", "  My%Cat  ", "x" * 20, "动画", "", "赛博朋克", 42])
    # 通配符剥除、限长 12、预设剔除、去重、非字符串丢弃、最多 3 个
    assert out == ["赛博朋克", "My Cat", "x" * 12]
    assert g.sanitize_custom_tags("not-a-list") == []
    assert g.sanitize_custom_tags(None) == []


def test_image_upload_with_custom_tags(client):
    resp = _upload_image(client, tags=["动画", "几何"],
                         custom=["赛博朋克", "我的猫"])
    assert resp.status_code == 200, resp.data
    work_id = json.loads(resp.data)["id"]
    params_work = client.get(f"/api/gallery/work/{work_id}")
    tags = json.loads(params_work.data)["tags"]
    assert tags == ["动画", "几何", "赛博朋克", "我的猫"]
    # 预设超 3 个仍被截断；自定义独立计数
    resp2 = _upload_image(client, title="T34b",
                          tags=["动画", "几何", "人像", "场景"],
                          custom=["a", "b", "c", "d"])
    tags2 = json.loads(client.get(
        f"/api/gallery/work/{json.loads(resp2.data)['id']}").data)["tags"]
    assert tags2 == ["动画", "几何", "人像", "a", "b", "c"]


def test_text_upload_with_custom_tags(client):
    resp = client.post("/api/gallery/upload-text", json={
        "art": "HELLO\nWORLD", "font": "ghost", "title": "T34 文字",
        "custom_tags": ["手绘", "test tag"]})
    assert resp.status_code == 200, resp.data
    work_id = json.loads(resp.data)["id"]
    detail = client.get(f"/api/gallery/work/{work_id}")
    tags = json.loads(detail.data)["tags"]
    assert tags == ["手绘", "test tag"]


# ── 自定义标签：计数 + 筛选 ─────────────────────────────────

def test_custom_tags_counts_and_filter(client):
    w1 = json.loads(_upload_image(client, title="w1",
                                  custom=["赛博朋克", "独角"]).data)["id"]
    w2 = json.loads(_upload_image(client, title="w2",
                                  custom=["赛博朋克"]).data)["id"]
    w3 = json.loads(_upload_image(client, title="w3",
                                  custom=["冷门"]).data)["id"]

    counts = json.loads(client.get("/api/gallery/custom-tags").data)
    tags = counts["tags"]
    assert tags[0] == {"tag": "赛博朋克", "count": 2}  # 热度降序
    tag_names = [t["tag"] for t in tags]
    assert "独角" in tag_names and "冷门" in tag_names
    assert "动画" not in tag_names  # 预设不计入

    # 多选 OR 筛选：命中任意标签
    listing = json.loads(client.get(
        "/api/gallery/list?tags=独角,冷门").data)
    ids = {w["id"] for w in listing["items"]}
    assert ids == {w1, w3}
    single = json.loads(client.get("/api/gallery/list?tags=赛博朋克").data)
    assert {w["id"] for w in single["items"]} == {w1, w2}
    # 无匹配
    none = json.loads(client.get("/api/gallery/list?tags=不存在").data)
    assert none["items"] == []


def test_private_works_excluded_from_counts(client):
    json.loads(_upload_image(client, title="pub",
                             custom=["公开标签"]).data)["id"]
    json.loads(_upload_image(client, title="priv", private="1",
                             custom=["隐私标签"]).data)
    counts = json.loads(client.get("/api/gallery/custom-tags").data)
    names = [t["tag"] for t in counts["tags"]]
    assert "公开标签" in names
    assert "隐私标签" not in names  # custom_tag_counts 只扫公开作品


def test_custom_tag_xss_neutralized_in_view_page(client):
    evil = '来源<script>alert(1)</script>'
    body = json.loads(_upload_image(client, title="xss",
                                    custom=[evil]).data)
    work_id = body["id"]
    page = client.get(f"/v/{work_id}")
    assert page.status_code == 200
    raw = page.get_data(as_text=True)
    assert "<script>alert(1)</script>" not in raw
