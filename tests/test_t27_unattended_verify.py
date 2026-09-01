"""T27 — 无人值守独立验证（对 fix/audit-remediation 修复的攻击性回归）。

定位：独立挑刺，不重复 t25/t26/test_output* 已覆盖的用例（引用见各节 docstring）。
新增攻击角度：

1. 路径穿越全谱系：绝对路径 / 混合分隔符 / 反斜杠目录 / 空主名 / NUL 字节，
   并校验落盘字节与上传内容一致（t26 只测了 ../../ 与 sub/dir 的 PNG）。
2. 尺寸钳制 fuzz：非整数 / 浮点串 / 空串 / 0x 前缀 / 空超长整数，一律不 500；
   /api/generate 的 JSON width=null|list|dict → 当前 TypeError 500（bug，xfail 锁定）。
3. 限流 429 双语"格式"断言（中文 + " / " + 英文），不与 t26 的逐字断言重复。
4. 413 处理器作用域：5 个重端点统一 JSON 双语（t26 只测 /api/upload）。
5. gallery_like：数组体 [1,2]（回归任务口径）+ dict 体非字符串 cookie 的
   类型混淆（原 sqlite3.ProgrammingError 500，已修复转正）。
6. task-frames：越界/畸形 id 不 500；源文件损坏后 404（t25 覆盖正常契约与 413）。
7. LRU：走真实 HTTP 链路（130 次 /api/preview 不同尺寸）验证 128 上限（t26 只测
   cache_put 单元层面）。
8. sweep：上传 → 生成 → 强制过期 → sweep_expired，HTTP 产生的真实产物被删
   （t26 只测 TaskStore 单元层面）。
9. admin 鉴权：错密码 403 / 对密码 200 / 空口令永不匹配 / per-work token 403。
10. 产物紧凑化：py 产物不含明文帧文本（解码正确性已由 test_output.py:36 覆盖）。
11. XSS 静态检查：innerHTML 插值点用户字段必须过 escapeHtml；escapeHtml 实现含
    引号转义；view_work <script> 内嵌用户数据必须走 tojson。
12. 双语抽查：i18n commit 0a5ac26 新增文案的 "中文 / English" 形态静态锚点。
13. /api/download 的 CWD 依赖缺陷（冒烟 5555 实测 500，xfail 锁定）。
"""

from __future__ import annotations

import io
import json
import os
import re

import pytest
from PIL import Image

from termify.taskstore import (
    CACHE,
    CACHE_MAX_ENTRIES,
    TaskStore,
    cache_clear_all,
    cache_get,
    cache_key,
    reset_store_for_tests,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """隔离：临时 CWD（uploads/tmp）+ 独立任务库 + 清空限流/缓存。"""
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t27.db"))

    import app as app_mod
    from termify.taskstore import get_store

    cache_clear_all()
    reset_store_for_tests()
    app_mod._RL_LOG.clear()
    # 与生产一致，重新挂 sweep hook（reset 会丢掉 import 时注册的那个）。
    get_store().set_sweep_hook(app_mod._sweep_stale_frame_dirs)
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


# --- 共享小工具 ---------------------------------------------------------------


def _gif_bytes(n_frames=2, w=8, h=4):
    buf = io.BytesIO()
    frames = [Image.new("RGB", (w, h), (i * 60, 100, 150)) for i in range(n_frames)]
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:],
                   duration=50, loop=0)
    buf.seek(0)
    return buf.read()


def _png_bytes(w=4, h=2, color=(10, 200, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    buf.seek(0)  # save 后流位置在末尾，必须回卷再读
    return buf.read()


def _upload(client, filename="x.png", content=None):
    return client.post(
        "/api/upload",
        data={"file": (io.BytesIO(content or _png_bytes()), filename)},
        content_type="multipart/form-data",
    )


def _assert_bilingual(msg: str) -> None:
    """"中文 / English" 单串格式：含 CJK、含 " / " 分隔、分隔后有英文字母。"""
    assert isinstance(msg, str) and msg, f"错误文案为空: {msg!r}"
    assert re.search(r"[\u4e00-\u9fff]", msg), f"缺中文部分: {msg!r}"
    assert " / " in msg, f"缺 \" / \" 双语分隔符: {msg!r}"
    tail = msg.split(" / ", 1)[1]
    assert re.search(r"[A-Za-z]", tail), f"缺英文部分: {msg!r}"


def _make_gallery_db(monkeypatch, tmp_path, work_id="wt27work0001",
                     admin_token="tok-t27"):
    """临时 GalleryDB 替换 app 模块级 GALLERY_DB（不碰真实画廊库）。"""
    import app as app_mod
    from termify.gallery import GalleryDB

    gdir = tmp_path / "gallerydata"
    gdir.mkdir(exist_ok=True)
    # 预置假源文件/缩略图/og，供删除路径走 os.remove 分支
    for suffix in (".png", "_thumb.gif", "_og.png"):
        (gdir / (work_id + suffix)).write_bytes(b"fake")

    db = GalleryDB(str(tmp_path / "gallery" / "g.db"))
    db.init_db()
    db.insert_work({
        "id": work_id,
        "title": "t",
        "description": "",
        "tags": "[]",
        "author": "a",
        "source_path": str(gdir / (work_id + ".png")),
        "thumbnail_path": str(gdir / (work_id + "_thumb.gif")),
        "og_path": str(gdir / (work_id + "_og.png")),
        "params_json": "{}",
        "is_private": 0,
        "admin_token": admin_token,
        "created_at": "2026-01-01T00:00:00",
        "ip": "127.0.0.1",
    })
    monkeypatch.setattr(app_mod, "GALLERY_DB", db)
    return db


# ═══ 1. 路径穿越（上传）══════════════════════════════════════════════════════

TRAVERSAL_GIF_PAYLOAD = _gif_bytes()


@pytest.mark.parametrize("evil", [
    "../../evil.gif",           # POSIX 相对穿越
    "..\\..\\evil.gif",         # Windows 反斜杠穿越
    "a/b.gif",                  # 子目录注入
    "a\\b.gif",                 # 反斜杠子目录
    "C:\\Windows\\evil.gif",    # 绝对路径（Windows 盘符）
    "/etc/evil.gif",            # 绝对路径（POSIX）
    "evil\x00.gif",             # NUL 字节
])
def test_upload_traversal_gif_filename_variants(client, evil):
    """恶意 GIF 文件名一律 200 + 服务端名 {task_id}.gif 落盘，穿越目标不存在。"""
    resp = _upload(client, filename=evil, content=TRAVERSAL_GIF_PAYLOAD)
    assert resp.status_code == 200, f"filename={evil!r} -> {resp.status_code}"
    task_id = json.loads(resp.data)["task_id"]
    saved = os.path.join("uploads", f"{task_id}.gif")
    assert os.path.isfile(saved), evil
    # 落盘字节必须与上传内容一致（未被改名逻辑破坏）
    with open(saved, "rb") as f:
        assert f.read() == TRAVERSAL_GIF_PAYLOAD
    # 任务库 filepath 指向 uploads/ 内的服务端生成名
    from termify.taskstore import get_store

    fp = os.path.abspath(get_store().get(task_id)["filepath"])
    assert os.path.dirname(fp) == os.path.abspath("uploads")
    # 常见穿越落点不得存在
    assert not os.path.exists("evil.gif")
    assert not os.path.exists(os.path.join("uploads", "..", "evil.gif"))


@pytest.mark.parametrize("bad", ["..", ".", "noext", ".gif", "....gif", "archive.txt"])
def test_upload_bad_names_rejected(client, bad):
    """无扩展名 / 纯点 / 全点主名（splitext 视为无扩展）/ 白名单外扩展 → 400。"""
    assert _upload(client, filename=bad).status_code == 400, bad


def test_upload_uppercase_gif_extension_normalised(client):
    """.GIF 大写扩展过白名单；ext 先 lower() 再拼服务端名（DB filepath 为证）。

    注：Windows 文件系统大小写不敏感，无法用 isfile 区分 {id}.gif 与 {id}.GIF，
    故以任务库 filepath 的扩展名为准。
    """
    resp = _upload(client, filename="photo.GIF", content=TRAVERSAL_GIF_PAYLOAD)
    assert resp.status_code == 200
    task_id = json.loads(resp.data)["task_id"]
    from termify.taskstore import get_store

    fp = get_store().get(task_id)["filepath"]
    assert os.path.basename(fp) == f"{task_id}.gif", fp
    assert os.path.isfile(fp)


def test_upload_batch_traversal_gif(client):
    """批量上传的穿越文件名同样服务端生成名；内容与任务都完整。"""
    resp = client.post(
        "/api/upload-batch",
        data={"files": [
            (io.BytesIO(TRAVERSAL_GIF_PAYLOAD), "../../evil.gif"),
            (io.BytesIO(TRAVERSAL_GIF_PAYLOAD), "..\\..\\evil2.gif"),
            (io.BytesIO(_png_bytes()), "sub/dir/ok.png"),
        ]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["errors"] == []
    assert len(body["task_ids"]) == 3
    ext_by_name = {"../../evil.gif": ".gif", "..\\..\\evil2.gif": ".gif",
                   "sub/dir/ok.png": ".png"}
    for r in body["task_ids"]:
        ext = ext_by_name[r["filename"]]
        saved = os.path.join("uploads", f"{r['task_id']}{ext}")
        assert os.path.isfile(saved), r["filename"]
    for stray in ("evil.gif", "evil2.gif"):
        assert not os.path.exists(stray)
        assert not os.path.exists(os.path.join("uploads", stray))


# ═══ 2. 尺寸钳制 fuzz ════════════════════════════════════════════════════════


def test_preview_size_fuzz_never_500(client):
    """畸形尺寸串只允许 200（钳制后）或 400，绝不 500。"""
    task_id = json.loads(_upload(client).data)["task_id"]
    cases = [
        ("99999", "99999", 200, 400, 400),
        ("0", "-5", 200, 1, 1),
        ("+80", " 24 ", 200, 80, 24),      # 前导符号 / 空白可 int()
        ("214748364799", "1", 200, 400, 1),  # 超大整数也要钳住
        ("abc", "24", 400, None, None),
        ("80.5", "24", 400, None, None),
        ("", "", 400, None, None),
        ("1e999", "24", 400, None, None),
        ("NaN", "24", 400, None, None),
        ("0x50", "24", 400, None, None),
    ]
    for w, h, status, ew, eh in cases:
        resp = client.get(f"/api/preview/{task_id}?width={w}&height={h}")
        assert resp.status_code == status, f"width={w!r} height={h!r}"
        assert resp.status_code != 500
        if status == 200:
            body = json.loads(resp.data)
            assert body["width"] == ew and body["height"] == eh, (w, h)


def test_generate_size_fuzz_bad_strings(client):
    """/api/generate JSON 里畸形字符串尺寸 → 400 或钳制 200，不 500。"""
    task_id = json.loads(_upload(client).data)["task_id"]
    for w, h, status in [("99999", "99999", 200), ("abc", "24", 400),
                         ("12.5", "24", 400), ("", "24", 400)]:
        resp = client.post("/api/generate", json={
            "task_id": task_id, "format": "html", "width": w, "height": h})
        assert resp.status_code == status, (w, h, resp.status_code)


@pytest.mark.parametrize("bad_width", [None, [1, 2], {"a": 1}])
@pytest.mark.xfail(strict=True, raises=TypeError,
                   reason="产品 bug：/api/generate 的 int() 只捕 ValueError，"
                          "JSON width/height 为 null/list/dict 时 TypeError → 500 "
                          "（app.py:1039）。修复后应 400。")
def test_generate_json_nonint_width_no_500(client, bad_width):
    """JSON 非整数 width 不应 500（期望 400），当前 TypeError 直接冒泡。"""
    task_id = json.loads(_upload(client).data)["task_id"]
    resp = client.post("/api/generate", json={
        "task_id": task_id, "format": "html", "width": bad_width})
    assert resp.status_code in (200, 400), resp.status_code


# ═══ 3. 限流 429 双语格式 ════════════════════════════════════════════════════


def test_upload_rate_limit_message_bilingual_format(client):
    """超过 10 次/分钟后 429：文案满足"中文 / English"格式（不逐字，防脆断）。"""
    for _ in range(10):
        assert _upload(client).status_code == 200
    resp = _upload(client)
    assert resp.status_code == 429
    body = json.loads(resp.data)
    _assert_bilingual(body["error"])
    # 语义关键词双保险：中文"频繁/太"+ 英文 limit/too many
    assert "频繁" in body["error"] or "太" in body["error"]
    assert re.search(r"limit|too many", body["error"], re.I)


# ═══ 4. 413 处理器作用域 ═════════════════════════════════════════════════════


# (endpoint, 与端点真实解析方式匹配的 content_type)
# 注：Flask/werkzeug 3.1 的 MAX_CONTENT_LENGTH 是"读体时"惰性强制——
# /api/generate、/api/fetch-url 用 get_json(silent=True)，octet-stream 体不会被
# 读取因而不触发 413（走正常 404/400 分支）；JSON 体才会读取并触发 413。
# 上传类端点解析 multipart/form，任何 content_type 都会触发。
@pytest.mark.parametrize("endpoint,content_type", [
    ("/api/upload", "application/octet-stream"),
    ("/api/upload-batch", "application/octet-stream"),
    ("/api/gallery/upload", "application/octet-stream"),
    ("/api/generate", "application/json"),
    ("/api/fetch-url", "application/json"),
])
def test_413_bilingual_json_on_all_heavy_endpoints(client, endpoint, content_type):
    """超 20MB 请求体打任意重端点 → 统一 JSON 双语 413，而非 HTML 错误页。"""
    resp = client.post(endpoint, data=b"0" * (21 * 1024 * 1024),
                       content_type=content_type)
    assert resp.status_code == 413, endpoint
    assert resp.content_type.startswith("application/json"), endpoint
    body = json.loads(resp.data)
    _assert_bilingual(body["error"])
    assert "20MB" in body["error"]


# ═══ 5. gallery_like ═════════════════════════════════════════════════════════


def test_gallery_like_array_body_no_500(client, monkeypatch, tmp_path):
    """Content-Type application/json + 体 [1,2]（数组）→ 不 500，正常点赞。"""
    _make_gallery_db(monkeypatch, tmp_path, work_id="wt27like0001")
    resp = client.post("/api/gallery/like/wt27like0001", data="[1,2]",
                       content_type="application/json")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["ok"] is True and body["liked"] is True and body["count"] == 1


@pytest.mark.parametrize("evil_cookie", ['[1,2]', '{"nested": true}'])
def test_gallery_like_dict_nonstring_cookie_no_500(client, monkeypatch,
                                                   tmp_path, evil_cookie):
    """dict 体 + 非字符串 cookie 不应 500（修复后 400 双语拒绝，
    原 sqlite3.ProgrammingError 500 已由 app.py gallery_like 的
    cookie 类型/长度守卫修复，xfail 已摘除转正）。"""
    _make_gallery_db(monkeypatch, tmp_path, work_id="wt27like0002")
    resp = client.post("/api/gallery/like/wt27like0002",
                       data=json.dumps({"cookie": json.loads(evil_cookie)}),
                       content_type="application/json")
    assert resp.status_code == 400, resp.status_code
    _assert_bilingual(json.loads(resp.data)["error"])


# ═══ 6. task-frames 越界与损坏源 ═════════════════════════════════════════════
# 正常契约（200 结构 / 不存在 404 双语 / 413 守卫 / video 帧目录）由
# tests/test_t25_task_frames.py 全覆盖，此处只补攻击角度。


@pytest.mark.parametrize("bad_id", [
    "../../etc/passwd",
    "..\\..\\win",
    "aaaaaaaaaaaa/../../x",
    "deadbeef1234%00",
    "ZZZZZZZZZZZZ",          # 合法长度但非法字符集
    "short",
])
def test_task_frames_malformed_ids_never_500(client, bad_id):
    """畸形/越界 task id → 路由或校验层拒绝（400/404），绝不 500。"""
    resp = client.get(f"/api/task-frames/{bad_id}")
    assert resp.status_code in (400, 404), f"id={bad_id!r} -> {resp.status_code}"


def test_task_frames_corrupted_source_image_404(client):
    """任务元数据在但源文件被换成垃圾字节 → 404（任务实质不可用），不 500。"""
    task_id = json.loads(_upload(client, filename="c.png").data)["task_id"]
    saved = os.path.join("uploads", f"{task_id}.png")
    assert os.path.isfile(saved)
    with open(saved, "wb") as f:
        f.write(b"\x00garbage-not-an-image")
    resp = client.get(f"/api/task-frames/{task_id}")
    assert resp.status_code == 404
    _assert_bilingual(json.loads(resp.data)["error"])


# ═══ 7. LRU 经真实 HTTP 链路 ═════════════════════════════════════════════════


def test_lru_cap_enforced_via_preview_requests(client):
    """130 次不同尺寸 /api/preview（真实 cache_put 路径）后缓存仍 ≤128，
    最早的键被淘汰、最新键保留。"""
    task_id = json.loads(_upload(client, filename="lru.gif",
                                 content=TRAVERSAL_GIF_PAYLOAD).data)["task_id"]
    first_key = cache_key(task_id, "binary", 1, 1)
    last_key = cache_key(task_id, "binary", 130, 130)
    for i in range(1, 131):
        resp = client.get(f"/api/preview/{task_id}?charset=binary&width={i}&height={i}")
        assert resp.status_code == 200, i
        assert json.loads(resp.data)["width"] == min(i, 400)
    assert len(CACHE) == CACHE_MAX_ENTRIES, f"缓存未封顶: {len(CACHE)}"
    assert cache_get(task_id, first_key) is None, "最旧条目未被淘汰"
    assert cache_get(task_id, last_key) is not None, "最新条目丢失"


# ═══ 8. sweep_expired 清 HTTP 产生的真实产物 ═════════════════════════════════


def test_sweep_expired_removes_http_created_artifacts(client):
    """上传 → 生成 py → 强制过期 → sweep_expired：uploads/ 源文件与
    tmp/<task_id>_*.py 都真的消失，DB 行删除。"""
    from termify.taskstore import get_store

    task_id = json.loads(_upload(client).data)["task_id"]
    src = os.path.join("uploads", f"{task_id}.png")
    resp = client.post("/api/generate", json={
        "task_id": task_id, "format": "python", "width": 8, "height": 2})
    assert resp.status_code == 200
    artifact = os.path.join("tmp", f"{task_id}_ascii.py")
    assert os.path.isfile(src) and os.path.isfile(artifact)

    task = get_store().get(task_id)
    get_store().put(  # 原字段原样写回，仅 TTL 置负 → 立即过期
        task_id, filepath=task["filepath"], original_size=task["original_size"],
        target_size=task["target_size"], frames_count=task["frames_count"],
        interval=task["interval"], ttl_seconds=-1)
    assert get_store().sweep_expired() == 1

    assert get_store().get(task_id) is None
    assert not os.path.exists(src), "uploads/ 源文件未被 sweep 清理"
    assert not os.path.exists(artifact), "tmp/ 下载产物未被 sweep 清理"


def test_sweep_store_isolation(tmp_path):
    """TaskStore 以传入路径独立建库（TERMIFY_TASK_DB 隔离口径）。"""
    db = str(tmp_path / "iso" / "sweep.db")
    store = TaskStore(db)
    store.init_db()
    store.put("cafebabecafe", filepath=None)
    assert store.exists("cafebabecafe")
    assert os.path.isfile(db)


# ═══ 9. admin 鉴权（compare_digest 行为面）═══════════════════════════════════


def test_admin_api_wrong_pwd_403_correct_200(client, monkeypatch):
    """/api/gallery/admin：无头/错密码 403，正确密码 200。"""
    monkeypatch.setenv("TERMIFY_ADMIN_PWD", "s3cret-t27")
    assert client.get("/api/gallery/admin").status_code == 403
    r = client.get("/api/gallery/admin",
                   headers={"X-Termify-Admin-Pwd": "wrong"})
    assert r.status_code == 403
    r = client.get("/api/gallery/admin",
                   headers={"X-Termify-Admin-Pwd": "s3cret-t27"})
    assert r.status_code == 200
    body = json.loads(r.data)
    assert "works" in body and "reports" in body


def test_admin_empty_pwd_never_matches(client, monkeypatch):
    """TERMIFY_ADMIN_PWD 未设置/为空时，空 header 也必须 403（空串不互认）。"""
    monkeypatch.delenv("TERMIFY_ADMIN_PWD", raising=False)
    assert client.get("/api/gallery/admin").status_code == 403
    assert client.get("/api/gallery/admin",
                      headers={"X-Termify-Admin-Pwd": ""}).status_code == 403
    monkeypatch.setenv("TERMIFY_ADMIN_PWD", "")
    assert client.get("/api/gallery/admin",
                      headers={"X-Termify-Admin-Pwd": ""}).status_code == 403


def test_gallery_delete_wrong_work_token_403_right_token_200(client,
                                                             monkeypatch,
                                                             tmp_path):
    """per-work admin_token：错 token 403；正确 token 经 compare_digest 200。"""
    _make_gallery_db(monkeypatch, tmp_path, work_id="wt27del00001",
                     admin_token="tok-correct")
    url = "/api/gallery/work/wt27del00001"
    assert client.delete(url, headers={"X-Termify-Admin": "tok-wrong"}).status_code == 403
    assert client.delete(url).status_code == 403
    resp = client.delete(url, headers={"X-Termify-Admin": "tok-correct"})
    assert resp.status_code == 200
    from app import GALLERY_DB

    assert GALLERY_DB.get_work("wt27del00001") is None


# ═══ 10. 产物紧凑化：明文不得残留 ════════════════════════════════════════════


# /api/download 正常路径（CWD == root_path 时 200）由
# tests/test_t7_web_api.py::test_api_download_serves_file 覆盖；它恰好因为
# pytest 从仓库根运行而通过，掩盖了下面的 CWD 依赖缺陷。


@pytest.mark.xfail(strict=True, raises=FileNotFoundError,
                   reason="产品 bug：/api/download（app.py:1158）用 CWD 相对路径 "
                          "send_file('tmp/<file>')，而 Flask send_file 把相对路径解析到 "
                          "app.root_path。CWD ≠ 仓库根时（systemd WorkingDirectory 不一致、"
                          "PyInstaller 启动器、隔离部署），generate 写到 $CWD/tmp 而 "
                          "download 去 <root_path>/tmp 找 → FileNotFoundError → 裸 HTML 500。"
                          "真实服务冒烟（隔离 CWD，端口 5555）实测 500。")
def test_download_cwd_independent(client):
    """CWD ≠ root_path 时 /api/download 不应 500（send_file 相对路径语义缺陷）。"""
    task_id = json.loads(_upload(client).data)["task_id"]
    resp = client.post("/api/generate", json={
        "task_id": task_id, "format": "python", "width": 8, "height": 2})
    assert resp.status_code == 200
    filename = json.loads(resp.data)["download_url"].split("/")[-1]
    # 本文件 autouse fixture 已 chdir 到 tmp_path（≠ root_path），正好复现
    resp = client.get(f"/api/download/{filename}")
    assert resp.status_code == 200



def test_py_artifact_no_plaintext_frames():
    """blocks py 产物必须只有 zlib+Base85 blob：明文帧内容不得出现在源码里。
    （解码后与原序列一致的断言见 tests/test_output.py::test_python_output_embeds_compact_frames_blob）
    """
    from termify.engine import FrameSequence
    from termify.output import render

    marker = "T27PLAINTEXT_CANARY_9F3A"
    lines = [marker + " red", marker + " blue"]
    seq = FrameSequence(lines_per_frame=[list(lines), list(lines)],
                        interval=0.05, width=16, height=2, charset="blocks")
    src = render(seq, "python")
    assert marker not in src, "帧文本明文残留在 py 产物中（未压缩？）"
    assert re.search(r'^FRAMES_B85 = "[A-Za-z0-9!#$%&()*+\-./;<>?@\[\]^_`{|}~]+"$',
                     src, re.M), "缺 FRAMES_B85 压缩 blob"


# ═══ 11. XSS 静态检查 ════════════════════════════════════════════════════════

_XSS_USER_FIELDS = (
    "title", "author", "description", "tags", "reason",
    "thumbnail_url", "og_url", "source_url",
)
_INNERHTML_FILES = [
    "templates/gallery.html", "templates/admin.html",
    "templates/view_work.html", "templates/index.html",
    "static/js/app.js", "static/js/termify-render.js",
]


def _read_repo(rel: str) -> str:
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_xss_user_fields_escaped_at_innerhtml():
    """innerHTML 插值行里出现用户可控字段时，同行必须过 escapeHtml。"""
    assignment_re = re.compile(r"\.innerHTML\s*=\s*(?!=)(.+);?\s*$")
    field_re = re.compile(
        r"\b\w+\.(?:" + "|".join(re.escape(f) for f in _XSS_USER_FIELDS) + r")\b"
    )
    sites = 0
    for rel in _INNERHTML_FILES:
        for lineno, line in enumerate(_read_repo(rel).splitlines(), 1):
            m = assignment_re.search(line)
            if not m:
                continue
            sites += 1
            if field_re.search(line) and "escapeHtml(" not in line:
                pytest.fail(
                    f"{rel}:{lineno} innerHTML 插值未过 escapeHtml: {line.strip()[:120]}")
    assert sites >= 10, f"innerHTML 检查点过少({sites})，规则可能失效"


def test_xss_escapehtml_impl_escapes_quotes():
    """三处 escapeHtml 实现必须连引号一起转义（属性上下文不可逃逸）。"""
    for rel in ("templates/gallery.html", "templates/admin.html",
                "templates/view_work.html"):
        src = _read_repo(rel)
        assert "function escapeHtml" in src, f"{rel} 缺 escapeHtml"
        assert 'replace(/"/g, "&quot;")' in src, f"{rel} 未转义双引号"
        assert "replace(/'/g, \"&#39;\")" in src, f"{rel} 未转义单引号"


def test_xss_view_work_script_embeds_user_data_via_tojson():
    """view_work.html 的 <script> 块内，work.id/title 只能经 | tojson 内嵌。"""
    lines = _read_repo("templates/view_work.html").splitlines()
    in_script = False
    for lineno, line in enumerate(lines, 1):
        if "<script" in line:
            in_script = True
        if "</script>" in line:
            in_script = False
            continue
        if not in_script:
            continue
        if re.search(r"\{\{\s*work\.(?:id|title)\b", line) and "tojson" not in line:
            pytest.fail(f"view_work.html:{lineno} <script> 内裸内嵌 work 数据: "
                        f"{line.strip()[:120]}")


# ═══ 12. 双语抽查（i18n commit 0a5ac26 新增文案锚点）═════════════════════════


@pytest.mark.parametrize("rel,needle", [
    ("static/js/app.js", "请先上传文件 / Please upload a file first"),
    ("static/js/app.js", "音乐文件过大（上限 20MB）/ Music file too large (max 20MB)"),
    ("static/js/app.js", "不支持的格式 / Unsupported format"),
    ("static/js/app.js", "支持 MP3/WAV/M4A/AAC/OGG/FLAC / Unsupported music format"),
    ("static/js/app.js", "音乐已就绪，导出时自动合成 / Music ready"),
    ("static/js/app.js", "下载动画文件 / Download animation"),
    ("templates/index.html", "下载动画文件 / Download animation"),
    ("app.py", "文件过大（上限 20MB） / File too large (max 20MB)"),
    ("app.py", "任务不存在 / Task not found"),
])
def test_bilingual_message_anchors(rel, needle):
    """新增用户可见文案必须是"中文 / English"单串形态。"""
    assert needle in _read_repo(rel), f"{rel} 缺双语锚点: {needle!r}"
