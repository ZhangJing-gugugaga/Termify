"""T17 在线画廊 + 短链分享 — 全链路测试覆盖。

覆盖 GOAL-PROMPT §11.⑤:
  - 上传作品 (multipart + 参数校验 + 速率限制)
  - 画廊列表 (分页 / 排序 / 标签筛选)
  - 作品详情 (含预填参数 + view 计数)
  - 点赞 / 取消点赞
  - 举报 + 管理员后台
  - 删除 (admin_token cookie / X-Termify-Admin header)
  - 速率限制 (上传 / 点赞 频率)
  - 私密链接 (is_private=1 仍在列表外但可访问)
  - 路径 traversal 防护
  - SQL 注入防护
  - OG 图 + 缩略图生成
  - 真实 SalaryCat 猫图端到端
"""

from __future__ import annotations

import io
import json
import os
import secrets

import pytest
from PIL import Image

pytestmark = pytest.mark.skipif(
    not __import__("importlib").util.find_spec("flask"),
    reason="flask 未安装",
)

# Valid presets imported from backend
VALID_TAGS = ["动画", "几何", "人像", "场景", "抽象", "像素艺术", "ASCII art"]
VALID_REPORT_REASONS = ["nsfw", "copyright", "spam", "other"]


@pytest.fixture(scope="function")
def isolated_env(tmp_path, monkeypatch):
    """Run each test in a clean tmp dir to avoid DB / file collisions."""
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    test_data = tmp_path / "termify_test_data"
    test_data.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_ADMIN_PWD", "test-admin-secret")

    # Replace app's db + data dir with per-test instances
    from app import app as flask_app
    from termify import gallery as _g
    db_path = str(test_data / "termify.db")
    new_db = _g.GalleryDB(db_path)
    new_db.init_db()
    monkeypatch.setattr("app.GALLERY_DATA_DIR", str(test_data))
    monkeypatch.setattr("app.GALLERY_DB", new_db)
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _png_bytes(w=64, h=48, color=(100, 150, 200), fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format=fmt)
    buf.seek(0)
    return buf


def _gif_bytes(frames=3, w=32, h=24):
    """Small multi-frame GIF."""
    buf = io.BytesIO()
    imgs = []
    for i in range(frames):
        imgs.append(Image.new("RGB", (w, h), (i * 80, 100, 200)))
    imgs[0].save(buf, format="GIF", save_all=True, append_images=imgs[1:],
                 duration=100, loop=0)
    buf.seek(0)
    return buf


_UPLOAD_COUNTER = [0]


def _upload(client, title="cat", desc="a cool cat", tags=None, author="tester",
           is_private="0", params=None, fname="cat.png", file_bytes=None,
           expect_status=200):
    """Helper: POST a multipart gallery upload, return parsed JSON body.

    Uses a unique X-Forwarded-For IP per call so rate limits don't collide
    across tests (each test gets its own IP bucket).
    """
    if tags is None:
        tags = ["动画"]
    if params is None:
        params = {"charset": "blocks", "width": 80, "height": 24}
    if file_bytes is None:
        file_bytes = _png_bytes()
    data = {
        "source": (file_bytes, fname),
        "title": title,
        "description": desc,
        "tags": json.dumps(tags),
        "author": author,
        "is_private": is_private,
        "params": json.dumps(params),
    }
    # UUID-based IP ensures each call uses a fresh rate-limit bucket
    # (avoids collision with fixed IPs used in rate-limit-focused tests).
    resp = client.post("/api/gallery/upload", data=data,
                       content_type="multipart/form-data",
                       headers={"X-Forwarded-For": secrets.token_hex(4) + ":" + secrets.token_hex(4)})
    assert resp.status_code == expect_status
    return json.loads(resp.data)


# ----------------------------------------------------------------------------
# 1. Upload
# ----------------------------------------------------------------------------

def test_upload_success_returns_work(isolated_env):
    body = _upload(isolated_env, title="My Cat", desc="lovely")
    assert body["ok"] is True
    assert "id" in body
    assert len(body["id"]) == 8
    assert body["admin_token"]
    assert "/v/" in body["url"]
    assert body["work"]["title"] == "My Cat"
    assert body["work"]["author"] == "tester"


def test_upload_without_title_uses_filename(isolated_env):
    body = _upload(isolated_env, title="", fname="funny.gif")
    # Empty title should still be ok — backend sanitizes to filename stem
    assert body["ok"] is True
    assert body["work"]["title"]  # Not empty


def test_upload_default_author_anonymous(isolated_env):
    body = _upload(isolated_env, author="")
    assert body["work"]["author"] == "匿名创作者"


def test_upload_rejects_non_image(isolated_env):
    fake = io.BytesIO(b"not an image at all!")
    body = _upload(isolated_env, file_bytes=fake, fname="evil.txt",
                   expect_status=400)
    assert "error" in body


def test_upload_rejects_corrupt_image(isolated_env):
    fake = io.BytesIO(b"\x89PNG\r\n\x1a\nGARBAGE_DATA")
    body = _upload(isolated_env, file_bytes=fake, fname="fake.png",
                   expect_status=400)
    assert "error" in body


def test_upload_rejects_empty_source(isolated_env):
    fake = io.BytesIO(b"")
    body = _upload(isolated_env, file_bytes=fake, fname="empty.png",
                   expect_status=400)
    assert "error" in body


def test_upload_tags_invalid_filtered_out(isolated_env):
    body = _upload(isolated_env, tags=["动画", "假的tag", "人像"])
    # only valid tags kept, capped at 3
    assert body["ok"] is True
    work = body["work"]
    assert "动画" in work["tags"]
    assert "假的tag" not in work["tags"]


def test_upload_tags_max_3(isolated_env):
    body = _upload(isolated_env, tags=["动画", "几何", "人像", "场景"])
    assert len(body["work"]["tags"]) <= 3


def test_upload_params_charset_invalid_falls_back(isolated_env):
    params = {"charset": "nonexistent", "width": 80, "height": 24}
    body = _upload(isolated_env, params=params)
    assert body["ok"] is True
    # Should have fallen back to default charset
    assert body["work"]["params"]["charset"] == "blocks"


def test_upload_private_flag_respected(isolated_env):
    body = _upload(isolated_env, is_private="1")
    assert body["ok"] is True
    # Verify via DB lookup directly
    from app import GALLERY_DB
    work = GALLERY_DB.get_work(body["id"])
    assert work["is_private"] == 1


# ----------------------------------------------------------------------------
# 2. List + pagination
# ----------------------------------------------------------------------------

def test_list_empty_when_no_uploads(isolated_env):
    resp = isolated_env.get("/api/gallery/list")
    body = json.loads(resp.data)
    assert body["total"] == 0
    assert body["items"] == []
    assert body["has_more"] is False


def test_list_returns_uploaded_works(isolated_env):
    _upload(isolated_env, title="A")
    _upload(isolated_env, title="B")
    resp = isolated_env.get("/api/gallery/list")
    body = json.loads(resp.data)
    assert body["total"] == 2
    assert len(body["items"]) == 2
    titles = [w["title"] for w in body["items"]]
    assert "A" in titles
    assert "B" in titles


def test_list_pagination_2_per_page(isolated_env):
    for i in range(5):
        _upload(isolated_env, title=f"work-{i}")
    resp = isolated_env.get("/api/gallery/list?page=1&limit=2")
    body = json.loads(resp.data)
    assert len(body["items"]) == 2
    assert body["total"] == 5
    assert body["has_more"] is True

    resp3 = isolated_env.get("/api/gallery/list?page=3&limit=2")
    body3 = json.loads(resp3.data)
    assert len(body3["items"]) == 1
    assert body3["has_more"] is False


def test_list_excludes_private_by_default(isolated_env):
    _upload(isolated_env, title="public", is_private="0")
    _upload(isolated_env, title="secret", is_private="1")
    resp = isolated_env.get("/api/gallery/list")
    body = json.loads(resp.data)
    titles = [w["title"] for w in body["items"]]
    assert "public" in titles
    assert "secret" not in titles


def test_list_sort_by_hot(isolated_env):
    body1 = _upload(isolated_env, title="liked")
    # Like it directly via DB so we can set like_count
    from app import GALLERY_DB
    # Insert a like manually
    GALLERY_DB.toggle_like(body1["id"], "10.0.0.1", "cookie")
    GALLERY_DB.get_work(body1["id"])  # bump

    _upload(isolated_env, title="unliked")
    resp = isolated_env.get("/api/gallery/list?sort=hot")
    body = json.loads(resp.data)
    assert body["items"][0]["id"] == body1["id"]


def test_list_filter_by_tag(isolated_env):
    _upload(isolated_env, title="几何图", tags=["几何"])
    _upload(isolated_env, title="动画图", tags=["动画"])
    resp = isolated_env.get("/api/gallery/list?tag=几何")
    body = json.loads(resp.data)
    assert body["total"] == 1
    assert body["items"][0]["title"] == "几何图"


# ----------------------------------------------------------------------------
# 3. Detail
# ----------------------------------------------------------------------------

def test_detail_returns_work_and_hides_admin_info(isolated_env):
    b = _upload(isolated_env, title="details test")
    resp = isolated_env.get(f"/api/gallery/work/{b['id']}")
    body = json.loads(resp.data)
    assert body["title"] == "details test"
    assert "admin_token" not in body
    assert "is_authorized" in body
    # View incremented
    assert body["view_count"] >= 1


def test_detail_not_found(isolated_env):
    resp = isolated_env.get("/api/gallery/work/nonexist")
    assert resp.status_code == 404


# ----------------------------------------------------------------------------
# 4. Like
# ----------------------------------------------------------------------------

def test_like_toggles(isolated_env):
    b = _upload(isolated_env)
    wid = b["id"]

    # Use a shared cookie jar so the same "voter" is recognised across calls
    cookies = {}
    def _post_like():
        headers = {}
        ck = cookies.get(f"termify_like_{wid}")
        if ck:
            headers["Cookie"] = f"termify_like_{wid}={ck}"
        resp = isolated_env.post(
            f"/api/gallery/like/{wid}",
            environ_base={"REMOTE_ADDR": "10.0.0.99"},
            headers=headers or None,
        )
        # Capture any cookie the server sets
        sc = resp.headers.get("Set-Cookie", "")
        if f"termify_like_{wid}=" in sc:
            val = sc.split(f"termify_like_{wid}=")[1].split(";")[0]
            cookies[f"termify_like_{wid}"] = val
        return resp

    # First like
    resp1 = _post_like()
    body1 = json.loads(resp1.data)
    assert body1["liked"] is True
    assert body1["count"] == 1

    # Same IP+cookie toggles off
    resp2 = _post_like()
    body2 = json.loads(resp2.data)
    assert body2["liked"] is False
    assert body2["count"] == 0


def test_like_unknown_work(isolated_env):
    resp = isolated_env.post("/api/gallery/like/nonexist")
    assert resp.status_code == 404


# ----------------------------------------------------------------------------
# 5. Report
# ----------------------------------------------------------------------------

def test_report_success(isolated_env):
    b = _upload(isolated_env)
    resp = isolated_env.post(f"/api/gallery/report/{b['id']}",
                            data=json.dumps({"reason": "spam", "description": "bot"}),
                            content_type="application/json")
    body = json.loads(resp.data)
    assert body["ok"] is True
    assert "report_id" in body


def test_report_invalid_reason(isolated_env):
    b = _upload(isolated_env)
    resp = isolated_env.post(f"/api/gallery/report/{b['id']}",
                            data=json.dumps({"reason": "wrong", "description": ""}),
                            content_type="application/json")
    assert resp.status_code == 400


def test_report_unknown_work(isolated_env):
    resp = isolated_env.post("/api/gallery/report/nonexist",
                            data=json.dumps({"reason": "spam"}),
                            content_type="application/json")
    assert resp.status_code == 404


# ----------------------------------------------------------------------------
# 6. Delete (admin token)
# ----------------------------------------------------------------------------

def test_delete_with_cookie_token(isolated_env):
    b = _upload(isolated_env)
    # Request sets admin_token cookie automatically; send it back
    resp = isolated_env.delete(f"/api/gallery/work/{b['id']}",
                              headers={"X-Termify-Admin": b["admin_token"]})
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["ok"] is True
    # Gone
    resp2 = isolated_env.get(f"/api/gallery/work/{b['id']}")
    assert resp2.status_code == 404


def test_delete_without_token_rejected(isolated_env):
    b = _upload(isolated_env)
    # Explicitly send a WRONG admin token (don't rely on cookie absence)
    resp = isolated_env.delete(f"/api/gallery/work/{b['id']}",
                              headers={"X-Termify-Admin": "wrong-token",
                                       "Cookie": ""})
    assert resp.status_code == 403


# ----------------------------------------------------------------------------
# 7. Admin dashboard
# ----------------------------------------------------------------------------

def test_admin_list_works(isolated_env):
    b = _upload(isolated_env)
    resp = isolated_env.get("/api/gallery/admin?pwd=test-admin-secret")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    ids = [w["id"] for w in body["works"]]
    assert b["id"] in ids


def test_admin_rejects_wrong_pwd(isolated_env):
    _upload(isolated_env)
    resp = isolated_env.get("/api/gallery/admin?pwd=wrong")
    assert resp.status_code == 403


# ----------------------------------------------------------------------------
# 8. Rate limiting
# ----------------------------------------------------------------------------

def test_upload_rate_limit_per_minute(isolated_env):
    """4 rapid uploads from same IP: 4th must be 429."""
    # 3 successes, then 4th blocked
    for _ in range(3):
        data = {
            "source": (_png_bytes(), "rl.png"),
            "title": "rl",
            "description": "",
            "tags": json.dumps(["动画"]),
            "author": "tester",
            "is_private": "0",
            "params": json.dumps({"charset": "blocks", "width": 80, "height": 24}),
        }
        r = isolated_env.post("/api/gallery/upload", data=data,
                              content_type="multipart/form-data",
                              headers={"X-Forwarded-For": "10.0.0.50"})
        assert r.status_code == 200
    data4 = {
        "source": (_png_bytes(), "rl.png"),
        "title": "rl",
        "description": "",
        "tags": json.dumps(["动画"]),
        "author": "tester",
        "is_private": "0",
        "params": json.dumps({"charset": "blocks", "width": 80, "height": 24}),
    }
    r4 = isolated_env.post("/api/gallery/upload", data=data4,
                           content_type="multipart/form-data",
                           headers={"X-Forwarded-For": "10.0.0.50"})
    body = json.loads(r4.data)
    assert r4.status_code == 429
    assert "rate limit" in body.get("error", "").lower()


# ----------------------------------------------------------------------------
# 9. File proxy safety
# ----------------------------------------------------------------------------

def test_thumb_returns_file(isolated_env):
    b = _upload(isolated_env, file_bytes=_gif_bytes())
    resp = isolated_env.get(f"/gallery/file/{b['id']}/thumb")
    assert resp.status_code == 200
    assert "image/gif" in resp.headers.get("Content-Type", "")


def test_thumb_path_traversal_blocked(isolated_env):
    """Attempt to read arbitrary file via 'source' path must be rejected."""
    # Non-existent work id -> 404
    resp = isolated_env.get("/gallery/file/../../../../etc/passwd/source")
    assert resp.status_code in (404, 400)


# ----------------------------------------------------------------------------
# 10. DB safety
# ----------------------------------------------------------------------------

def test_list_sql_injection_safe(isolated_env):
    """Tag-based filter must not execute arbitrary SQL."""
    _upload(isolated_env, title="normal", tags=["动画"])
    # Try various injection patterns
    for payload in ["'; DROP TABLE works; --", "1 OR 1=1", "' OR '1'='1"]:
        resp = isolated_env.get(f"/api/gallery/list?tag={payload}")
        # Should not crash — should just return empty or no-match
        assert resp.status_code == 200


# ----------------------------------------------------------------------------
# 11. Page routes
# ----------------------------------------------------------------------------

def test_gallery_page_renders(isolated_env):
    resp = isolated_env.get("/gallery")
    assert resp.status_code == 200
    text = resp.data.decode("utf-8", errors="ignore")
    assert "<!DOCTYPE html>" in text
    # Contains a marker from the template
    assert "在线画廊" in text or "gallery" in text.lower()


def test_view_page_renders(isolated_env):
    b = _upload(isolated_env, title="viewme")
    resp = isolated_env.get(f"/v/{b['id']}")
    assert resp.status_code == 200
    text = resp.data.decode("utf-8", errors="ignore")
    assert "viewme" in text


def test_view_page_not_found(isolated_env):
    resp = isolated_env.get("/v/nonexist")
    assert resp.status_code == 404


def test_admin_page_renders(isolated_env):
    resp = isolated_env.get("/admin")
    assert resp.status_code == 200


# ----------------------------------------------------------------------------
# 12. Thumbnail / OG generation
# ----------------------------------------------------------------------------

def test_thumbnail_created_on_upload(isolated_env):
    b = _upload(isolated_env, file_bytes=_gif_bytes())
    from app import GALLERY_DB
    work = GALLERY_DB.get_work(b["id"])
    assert os.path.isfile(work["thumbnail_path"])
    assert os.path.isfile(work["og_path"])
    with Image.open(work["thumbnail_path"]) as im:
        assert im.format == "GIF"
    with Image.open(work["og_path"]) as im:
        assert im.format == "PNG"


# ----------------------------------------------------------------------------
# 13. Real Image (SalaryCat cat.GIF) end-to-end
# ----------------------------------------------------------------------------

REAL_GIF = r"E:\Desktop\工作\SalaryCat\cat.GIF"


@pytest.mark.skipif(not os.path.isfile(REAL_GIF), reason="SalaryCat cat.GIF not on disk")
def test_real_cat_gif_upload_and_list(isolated_env):
    """Full round-trip with real SalaryCat cat.GIF."""
    with open(REAL_GIF, "rb") as fh:
        raw = fh.read()
    buf = io.BytesIO(raw)
    body = _upload(isolated_env, title="真实猫图", desc="来自 SalaryCat 的真实测试猫",
                   author="SalaryCat", tags=["动画"], fname="cat.GIF",
                   file_bytes=buf)
    assert body["ok"]
    assert body["work"]["title"] == "真实猫图"
    # Thumbnail + OG generated
    from app import GALLERY_DB
    work = GALLERY_DB.get_work(body["id"])
    assert os.path.isfile(work["thumbnail_path"])
    assert os.path.isfile(work["og_path"])
    # Appears in list
    resp = isolated_env.get("/api/gallery/list")
    body2 = json.loads(resp.data)
    assert body2["total"] >= 1
    ids = [w["id"] for w in body2["items"]]
    assert body["id"] in ids
    # Detail viewable
    resp3 = isolated_env.get(f"/api/gallery/work/{body['id']}")
    assert resp3.status_code == 200


# ----------------------------------------------------------------------------
# 14. Preview API
# ----------------------------------------------------------------------------

def test_gallery_preview_returns_frames(isolated_env):
    """Upload → GET /api/gallery/preview/<id> → 200 + non-empty frames."""
    b = _upload(isolated_env, params={"charset": "blocks", "width": 80, "height": 24})
    resp = isolated_env.get(f"/api/gallery/preview/{b['id']}")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert "frames" in body
    assert len(body["frames"]) >= 1
    assert body["charset"] == "blocks"
    assert body["width"] == 80
    assert body["height"] == 24
    assert "interval" in body


def test_gallery_preview_invalid_charset(isolated_env):
    b = _upload(isolated_env)
    resp = isolated_env.get(f"/api/gallery/preview/{b['id']}?charset=invalid")
    assert resp.status_code == 400


def test_gallery_preview_unknown_work(isolated_env):
    resp = isolated_env.get("/api/gallery/preview/nonexist")
    assert resp.status_code == 404


def test_gallery_preview_switch_charset(isolated_env):
    """Upload as blocks → switch to ascii → frames should differ."""
    b = _upload(isolated_env, params={"charset": "blocks", "width": 40, "height": 20})
    r_blocks = isolated_env.get(f"/api/gallery/preview/{b['id']}?charset=blocks&width=40&height=20")
    r_ascii = isolated_env.get(f"/api/gallery/preview/{b['id']}?charset=ascii&width=40&height=20")
    assert r_blocks.status_code == 200 and r_ascii.status_code == 200
    fb = json.loads(r_blocks.data)["frames"]
    fa = json.loads(r_ascii.data)["frames"]
    # Different charsets should yield different rendered output
    assert fb != fa


# ----------------------------------------------------------------------------
# 15. Download API
# ----------------------------------------------------------------------------

def test_gallery_download_python(isolated_env):
    b = _upload(isolated_env, params={"charset": "blocks", "width": 80, "height": 24})
    resp = isolated_env.get(f"/api/gallery/download/{b['id']}?format=python&charset=blocks&width=80&height=24")
    assert resp.status_code == 200
    text = resp.data.decode("utf-8", errors="ignore")
    # Self-contained .py with ANSI color escapes (stored as JSON \u001b[ which the player parses at runtime)
    assert "\\u001b[" in text
    assert "import" in text
    assert "def play" in text or "def render" in text


def test_gallery_download_html(isolated_env):
    b = _upload(isolated_env)
    resp = isolated_env.get(f"/api/gallery/download/{b['id']}?format=html")
    assert resp.status_code == 200
    text = resp.data.decode("utf-8", errors="ignore")
    assert "<!DOCTYPE" in text or "<html" in text
    assert "</html>" in text


def test_gallery_download_invalid_format(isolated_env):
    b = _upload(isolated_env)
    resp = isolated_env.get(f"/api/gallery/download/{b['id']}?format=pdf")
    assert resp.status_code == 400


def test_gallery_download_unknown_work(isolated_env):
    resp = isolated_env.get("/api/gallery/download/nonexist?format=python")
    assert resp.status_code == 404


def test_gallery_download_count_increments(isolated_env):
    """First download +1; same IP repeat within 24h does not double-count."""
    b = _upload(isolated_env)
    from app import GALLERY_DB
    assert GALLERY_DB.get_work(b["id"])["download_count"] == 0
    r1 = isolated_env.get(f"/api/gallery/download/{b['id']}?format=python")
    assert r1.status_code == 200
    assert GALLERY_DB.get_work(b["id"])["download_count"] == 1
    # Same IP downloads again within 24h → no double count
    r2 = isolated_env.get(f"/api/gallery/download/{b['id']}?format=python")
    assert r2.status_code == 200
    assert GALLERY_DB.get_work(b["id"])["download_count"] == 1
    # Different IP → counts again
    r3 = isolated_env.get(f"/api/gallery/download/{b['id']}?format=python",
                         headers={"X-Forwarded-For": "10.0.0.200"})
    assert r3.status_code == 200
    assert GALLERY_DB.get_work(b["id"])["download_count"] == 2


def test_view_work_page_no_upload_section(isolated_env):
    """GET /v/<id> must NOT contain '继续创作' / 'uploadZone' / '已发布'."""
    b = _upload(isolated_env)
    resp = isolated_env.get(f"/v/{b['id']}")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "继续创作" not in html
    assert "uploadZone" not in html
    assert "已发布" not in html
    # But should still contain style selection + preview sections
    assert "选择风格" in html
    assert "animPreview" in html
    assert "下载 .py" in html
    assert "下载 .html" in html
    assert "复制链接" in html


# ----------------------------------------------------------------------------
# 16. View page UX bug fixes
# ----------------------------------------------------------------------------

def test_view_page_has_5_size_buttons(isolated_env):
    """Bug 2: size selector must have 5 preset buttons (40x20 → 200x60)."""
    b = _upload(isolated_env)
    resp = isolated_env.get(f"/v/{b['id']}")
    html = resp.data.decode("utf-8", errors="ignore")
    for size in ["40x20", "80x24", "120x36", "160x48", "200x60"]:
        assert f'data-size="{size}"' in html, f"missing size button {size}"


def test_view_page_no_share_link_on_home(isolated_env):
    """Bug 4: 主页 '/' 不得存在 share-link 假链接 + 复制按钮."""
    resp = isolated_env.get("/")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert 'class="share-link"' not in html
    assert "termify.dev/s/" not in html


def test_view_page_has_opacity_transition(isolated_env):
    """Bug 5: loadPreview 函数包含 opacity 过渡逻辑."""
    b = _upload(isolated_env)
    resp = isolated_env.get(f"/v/{b['id']}")
    html = resp.data.decode("utf-8", errors="ignore")
    # loadPreview should set opacity 0.5 during loading and restore to 1
    assert "opacity 0.15s ease" in html
    assert 'preview.style.opacity = "0.5"' in html
    assert 'preview.style.opacity = "1"' in html


def test_view_page_ansi_to_html_uses_fg_bg(isolated_env):
    """Bug 3: ansiToHtml flush 必须使用闭包 fg/bg（不是 bufFg/bufBg）."""
    b = _upload(isolated_env)
    resp = isolated_env.get(f"/v/{b['id']}")
    html = resp.data.decode("utf-8", errors="ignore")
    # Should reference fg/bg in flush, NOT bufFg/bufBg (which were never assigned)
    assert "bufFg" not in html
    assert "bufBg" not in html
    # flush should use closure fg/bg for color
    assert 'if (fg || bg)' in html


def test_view_page_size_btn_classname(isolated_env):
    """Bug 1: size buttons toggle 'active' class (not 'selected')."""
    b = _upload(isolated_env)
    resp = isolated_env.get(f"/v/{b['id']}")
    html = resp.data.decode("utf-8", errors="ignore")
    # Click handler should remove 'active' (not 'selected')
    assert 'b.classList.remove("active")' in html
    assert 'b.classList.remove("selected")' not in html.replace('c.classList.remove("selected")', "")


# ----------------------------------------------------------------------------
# 17. Like persistence
# ----------------------------------------------------------------------------

def test_work_detail_includes_is_liked_true_after_liking(isolated_env):
    """After liking + re-fetching work detail, is_liked must be True."""
    b = _upload(isolated_env)
    wid = b["id"]
    # Like via the helper (sets cookie via Set-Cookie)
    like_resp = isolated_env.post(f"/api/gallery/like/{wid}",
                                  headers={"X-Forwarded-For": "10.0.0.88"})
    assert like_resp.status_code == 200
    body = json.loads(like_resp.data)
    assert body["liked"] is True
    assert body["count"] == 1
    # Extract the like cookie
    set_cookie = like_resp.headers.get("Set-Cookie", "")
    assert f"termify_like_{wid}=" in set_cookie
    cookie_val = set_cookie.split(f"termify_like_{wid}=")[1].split(";")[0]
    # Re-fetch work detail WITH the cookie + same IP → is_liked must be True
    detail = isolated_env.get(f"/api/gallery/work/{wid}",
                             headers={"Cookie": f"termify_like_{wid}={cookie_val}",
                                      "X-Forwarded-For": "10.0.0.88"})
    detail_body = json.loads(detail.data)
    assert detail_body.get("is_liked") is True


def test_work_detail_is_liked_false_without_cookie(isolated_env):
    """Without the like cookie, is_liked must be False."""
    b = _upload(isolated_env)
    resp = isolated_env.get(f"/api/gallery/work/{b['id']}")
    body = json.loads(resp.data)
    assert body.get("is_liked") is False


def test_view_page_fetches_like_state_on_load(isolated_env):
    """view_work.html JS must fetch work detail to restore liked state."""
    b = _upload(isolated_env)
    resp = isolated_env.get(f"/v/{b['id']}")
    html = resp.data.decode("utf-8", errors="ignore")
    # JS should fetch /api/gallery/work/<id> to restore liked state
    assert '"/api/gallery/work/" + WORK_ID' in html
    assert "is_liked" in html


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
