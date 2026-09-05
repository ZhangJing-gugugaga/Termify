"""OpenAI-compatible LLM client + server-side credential store.

凭证只落服务端磁盘（``data/llm_config.json``，data/ 已在 .gitignore），
任何接口都不会把 api_key 回传给浏览器；上游错误只暴露状态码等非敏感摘要，
响应体细节仅进服务端日志（且日志中永不含 key）。

端点约定：base_url 填到版本根目录，例如
- 智谱 GLM: https://open.bigmodel.cn/api/paas/v4
- Ollama:   http://localhost:11434/v1
- DeepSeek: https://api.deepseek.com/v1
最终请求 ``{base_url}/chat/completions``（OpenAI Chat Completions 协议）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "llm_config.json"

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4-flash"

TIMEOUT_S = 60
MAX_COMPLETION_CHARS = 12000   # 上游返回内容上限（防失控响应）
MAX_TOKENS = 2000

_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


class LLMError(RuntimeError):
    """Bilingual user-safe failure; ``cause`` stays server-side."""


def config_path(data_dir: str) -> str:
    return os.path.join(data_dir, CONFIG_FILENAME)


def load_config(data_dir: str) -> dict:
    """Return {base_url, model, api_key} — empty strings when unset/invalid."""
    cfg = {"base_url": "", "model": "", "api_key": ""}
    try:
        with open(config_path(data_dir), "r", encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return cfg
    if not isinstance(stored, dict):
        return cfg
    for k in cfg:
        v = stored.get(k)
        if isinstance(v, str):
            cfg[k] = v.strip()
    return cfg


def is_configured(cfg: dict) -> bool:
    return bool(cfg.get("base_url") and cfg.get("model"))


def save_config(data_dir: str, *, base_url: str, model: str,
                api_key: str | None) -> dict:
    """Validate + persist. ``api_key=None``（缺省）保留旧 key；空字符串清除。

    Returns the stored config. Raises LLMError on invalid input.
    """
    base_url = (base_url or "").strip().rstrip("/")
    model = (model or "").strip()
    if not base_url or not _URL_RE.match(base_url + "/"):
        raise LLMError("base_url 必须是 http(s) 链接 / base_url must be an "
                       "http(s) URL")
    if not model:
        raise LLMError("model 不能为空 / model is required")
    stored = load_config(data_dir)
    stored["base_url"] = base_url
    stored["model"] = model
    if api_key is not None:
        api_key = api_key.strip()
        stored["api_key"] = api_key  # 允许空串（Ollama 等本地端点无需 key）
    path = config_path(data_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(stored, fh, ensure_ascii=False)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows/部分文件系统不支持 POSIX 权限位
    return stored


def config_summary(cfg: dict) -> dict:
    """Client-safe projection — never includes the api key itself."""
    return {
        "configured": is_configured(cfg),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "has_key": bool(cfg.get("api_key")),
    }


def chat(messages: list[dict], cfg: dict, *, temperature: float = 0.4,
         max_tokens: int = MAX_TOKENS,
         extra_payload: dict | None = None) -> str:
    """Blocking chat-completions call. Returns assistant text content.

    ``extra_payload`` merges extra top-level request fields (e.g.
    ``{"chat_template_kwargs": {"enable_thinking": False}}`` to disable
    reasoning chains on thinking models — verified working on LongCat).
    Raises LLMError with user-safe messages; underlying response bodies are
    logged (truncated) server-side only and never include the api key.
    """
    if not is_configured(cfg):
        raise LLMError("请先配置 LLM 服务 / Configure the LLM service first")
    url = cfg["base_url"] + "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if extra_payload:
        payload.update(extra_payload)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = "Bearer " + cfg["api_key"]
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read(MAX_COMPLETION_CHARS * 4 + 16)
    except urllib.error.HTTPError as exc:
        detail = b""
        try:
            detail = exc.read(2048)
        except OSError:
            pass
        logger.warning("LLM HTTP %s from upstream: %.200s", exc.code,
                       detail.decode("utf-8", "replace"))
        if exc.code == 401:
            raise LLMError("LLM 鉴权失败，请检查 API key / LLM auth failed, "
                           "check the API key") from exc
        if exc.code == 429:
            raise LLMError("LLM 请求过于频繁，请稍后再试 / LLM rate limited, "
                           "retry later") from exc
        raise LLMError(f"LLM 服务返回错误（{exc.code}）/ LLM upstream error "
                       f"({exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("LLM unreachable: %s", exc)
        raise LLMError("无法连接 LLM 服务 / Cannot reach the LLM service") from exc
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
        content = data["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        logger.warning("LLM malformed response: %.400s",
                       raw.decode("utf-8", "replace"))
        raise LLMError("LLM 返回格式异常 / LLM returned a malformed "
                       "response") from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMError("LLM 返回为空 / LLM returned empty content")
    return content[:MAX_COMPLETION_CHARS]


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_object(text: str) -> dict:
    """Extract the first JSON object from a model reply; raises LLMError."""
    m = _JSON_OBJ_RE.search(text)
    if not m:
        raise LLMError("AI 未返回有效配置 / AI did not return a valid config")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError("AI 返回的配置无法解析，请重试 / AI config not "
                       "parseable, retry") from exc
    if not isinstance(obj, dict):
        raise LLMError("AI 返回的配置无法解析，请重试 / AI config not "
                       "parseable, retry")
    return obj
