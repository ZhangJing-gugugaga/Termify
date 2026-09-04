"""T35 — 字体墙 API 与预览渲染。"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from app import app  # noqa: E402


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_fontwall_renders_all_fonts(client):
    resp = client.post("/api/text/fontwall", json={"text": "hello"})
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["ok"] and len(data["fonts"]) >= 20
    f0 = data["fonts"][0]
    assert {"slug", "name", "art"} <= set(f0.keys())
    assert "\n" in f0["art"]  # 多行字形
    # 预览尺寸克制：行数不超上限
    for f in data["fonts"]:
        assert len(f["art"].split("\n")) <= 8


def test_fontwall_rejects_chinese(client):
    resp = client.post("/api/text/fontwall", json={"text": "你好"})
    assert resp.status_code == 400


def test_fontwall_truncates_long_text(client):
    resp = client.post("/api/text/fontwall", json={"text": "a" * 40})
    assert resp.status_code == 200
    arts = json.loads(resp.data)["fonts"]
    assert len(arts) >= 20  # 截断到 10 字符仍可渲染
