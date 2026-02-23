# -*- coding: utf-8 -*-
"""
WeKnora 对话记忆：从 WeKnora 检索相关历史片段，并将新轮次写入记忆知识库，以支持长文本上下文。
"""
import tempfile
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

WEKNORA_SEARCH_PATH = "/api/v1/knowledge-search"
WEKNORA_KNOWLEDGE_FILE_PATH = "/api/v1/knowledge-bases/{kb_id}/knowledge/file"
WEKNORA_TIMEOUT = 25
MEMORY_RETRIEVE_TOP_K = 8
MEMORY_INJECT_MAX_CHARS = 4500


def _get_memory_config():
    """返回 (base_url, api_key, memory_kb_id, enabled)。需在 Flask 请求上下文中。"""
    try:
        from flask import current_app
        load = current_app.config.get("CONFIG_LOADER")
        if not load:
            return None, None, None, False
        cfg = load()
        if not cfg.get("weknora_memory_enabled"):
            return None, None, None, False
        base = (cfg.get("weknora_base_url") or "").strip().rstrip("/")
        if not base:
            return None, None, None, False
        kb_id = (cfg.get("weknora_memory_kb_id") or "").strip() or None
        if not kb_id:
            return None, None, None, False
        api_key = (cfg.get("weknora_api_key") or "").strip() or None
        return base, api_key, kb_id, True
    except Exception:
        return None, None, None, False


def retrieve_memory(query: str, top_k: int = MEMORY_RETRIEVE_TOP_K) -> list:
    """
    根据当前问题从 WeKnora 记忆知识库检索相关片段。
    返回 [{"source": str, "text": str}, ...]，无配置或失败时返回 []。
    """
    base, api_key, kb_id, enabled = _get_memory_config()
    if not enabled or not base or not kb_id or not requests:
        return []
    query = (query or "").strip()
    if not query:
        return []
    url = base + WEKNORA_SEARCH_PATH
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    payload = {"query": query, "knowledge_base_id": kb_id}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=WEKNORA_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []
    if not data.get("success") or "data" not in data:
        return []
    raw_list = data.get("data") or []
    results = []
    for item in raw_list[:top_k]:
        if isinstance(item, dict):
            content = item.get("content") or ""
            source = item.get("knowledge_filename") or item.get("knowledge_title") or "记忆"
            results.append({"source": source, "text": content})
    return results


def format_memory_for_prompt(chunks: list, max_chars: int = MEMORY_INJECT_MAX_CHARS) -> str:
    """将检索到的记忆块格式化为可插入 prompt 的一段文字。"""
    if not chunks:
        return ""
    parts = []
    total = 0
    for c in chunks:
        text = (c.get("text") or "").strip()
        if not text:
            continue
        if total + len(text) + 2 > max_chars:
            remain = max_chars - total - 20
            if remain > 0:
                parts.append(text[:remain] + "...")
            break
        parts.append(text)
        total += len(text) + 2
    if not parts:
        return ""
    return "以下是与当前问题相关的历史对话或任务片段，供参考：\n\n" + "\n\n---\n\n".join(parts)


def append_turn_to_memory(conversation_id: str, turn_text: str) -> bool:
    """
    将一轮对话的文本追加写入 WeKnora 记忆知识库（通过上传临时文件）。
    turn_text 应为该轮的摘要或完整 user/assistant/tool 文本。
    成功返回 True，否则 False。
    """
    base, api_key, kb_id, enabled = _get_memory_config()
    if not enabled or not base or not kb_id or not requests:
        return False
    turn_text = (turn_text or "").strip()
    if not turn_text or len(turn_text) > 100000:
        return False
    url = base + WEKNORA_KNOWLEDGE_FILE_PATH.format(kb_id=kb_id)
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".md",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write("# 对话记忆\n\n")
            f.write("conversation_id: " + conversation_id + "\n\n")
            f.write(turn_text)
            tmp_path = f.name
        with open(tmp_path, "rb") as f:
            files = {"file": ("memory-" + conversation_id[:8] + ".md", f, "text/markdown")}
            r = requests.post(url, files=files, headers=headers, timeout=WEKNORA_TIMEOUT)
        Path(tmp_path).unlink(missing_ok=True)
        return r.status_code in (200, 201) and (r.json() or {}).get("success") is True
    except Exception:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except NameError:
            pass
        return False
