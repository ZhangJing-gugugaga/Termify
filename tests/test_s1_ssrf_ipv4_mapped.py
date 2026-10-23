"""S1 — SSRF 黑名单可被 IPv4-mapped IPv6 绕过（安全回归）。

2026-10-23 全功能重测确认：getaddrinfo('[::ffff:127.0.0.1]') 原样返回
"::ffff:127.0.0.1"，它与 IPv4 网段是不同 version，"ip in net" 恒为 False，
于是 127.0.0.0/8、169.254.0.0/16、10.0.0.0/8 甚至阿里云元数据
100.100.100.200 全部漏网。修复：判网段前先用 ipv4_mapped 还原成 IPv4。
"""

from __future__ import annotations

import json

import pytest

from termify.urlfetch import URLFetchError, _check_resolved_addr, validate_url


# 每个条目：URL 里的 host 字面量（IPv6 需方括号）。
MAPPED_PRIVATE_HOSTS = [
    "[::ffff:127.0.0.1]",        # loopback，纯 127.0.0.1 会被 127.0.0.0/8 拦住
    "[::ffff:169.254.169.254]",  # AWS/云厂商元数据
    "[::ffff:10.0.0.1]",         # 私网 10/8
    "[::ffff:100.100.100.200]",  # 阿里云内网元数据
]


@pytest.mark.parametrize("host", MAPPED_PRIVATE_HOSTS)
def test_ipv4_mapped_private_address_is_blocked(host):
    with pytest.raises(URLFetchError):
        validate_url(f"http://{host}/secret.png")


@pytest.mark.parametrize("host", MAPPED_PRIVATE_HOSTS)
def test_ipv4_mapped_private_address_is_blocked_with_port(host):
    """带端口走的是同一解析结果，不被当作另一条路径。"""
    with pytest.raises(URLFetchError):
        validate_url(f"http://{host}:8080/secret.png")


def test_ipv4_mapped_public_address_is_not_blocked():
    """公网 mapped 地址不得误拦（8.8.8.8 不在任何黑名单网段）。"""
    assert validate_url("http://[::ffff:8.8.8.8]/logo.png") == \
        "http://[::ffff:8.8.8.8]/logo.png"


def test_bare_alibaba_metadata_ip_is_blocked():
    """100.100.100.200 此前不在黑名单里，现补入。"""
    with pytest.raises(URLFetchError):
        validate_url("http://100.100.100.200/latest/meta-data/")


def test_bare_private_ipv4_still_blocked():
    """原有 IPv4 判定未被改坏。"""
    for url in ("http://127.0.0.1/x.png", "http://169.254.169.254/x.png",
                "http://192.168.1.1/x.png", "http://10.1.2.3/x.png"):
        with pytest.raises(URLFetchError):
            validate_url(url)


def test_check_resolved_addr_unwraps_mapped_ipv4():
    """还原规则的单测：mapped 地址按内部 IPv4 判，公网 mapped 放行。"""
    _check_resolved_addr("::ffff:8.8.8.8")            # 公网，放行
    _check_resolved_addr("2001:4860:4860::8888")      # 公网纯 IPv6，放行
    for addr in ("::ffff:127.0.0.1", "::ffff:192.168.1.1", "::1", "127.0.0.1"):
        with pytest.raises(URLFetchError):
            _check_resolved_addr(addr)


# --- 端点级：/api/fetch-url 必须把 mapped 内网地址当 400 拒掉 ----------------

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:  # pragma: no cover — 无 flask 的裸环境
    _HAVE_FLASK = False


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    from app import app, _RL_LOG
    _RL_LOG.clear()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.skipif(not _HAVE_FLASK, reason="flask 未安装")
def test_fetch_url_endpoint_rejects_mapped_loopback(client):
    resp = client.post("/api/fetch-url",
                       data=json.dumps({"url": "http://[::ffff:127.0.0.1]/a.png"}),
                       content_type="application/json")
    assert resp.status_code == 400
    body = resp.get_json()
    assert "internal" in body["error"].lower() or "private" in body["error"].lower()


@pytest.mark.skipif(not _HAVE_FLASK, reason="flask 未安装")
def test_fetch_url_endpoint_rejects_mapped_metadata_ip(client):
    resp = client.post("/api/fetch-url",
                       data=json.dumps({"url": "http://[::ffff:100.100.100.200]/latest/"}),
                       content_type="application/json")
    assert resp.status_code == 400
