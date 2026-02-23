# -*- coding: utf-8 -*-
"""
知识库：支持两种后端
1) WeKnora（推荐）：配置 weknora_base_url 后，使用腾讯开源 WeKnora 的语义检索 API（/api/v1/knowledge-search）
2) 本地：从 knowledge/ 目录加载 .md/.txt，按段落切块，关键词检索
供 search_knowledge 工具调用，便于 AI 在 CTF 等场景下查阅资料。
"""
import re
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"
CHUNK_MAX = 500
TOP_K_DEFAULT = 5
WEKNORA_SEARCH_PATH = "/api/v1/knowledge-search"
WEKNORA_TIMEOUT = 30


def _ensure_dir():
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)


def _get_weknora_config():
    """从应用配置读取 WeKnora 相关配置（需在 Flask 请求上下文中）。"""
    try:
        from flask import current_app
        load = current_app.config.get("CONFIG_LOADER")
        if not load:
            return None, None, None
        cfg = load()
        base = (cfg.get("weknora_base_url") or "").strip().rstrip("/")
        if not base:
            return None, None, None
        api_key = (cfg.get("weknora_api_key") or "").strip()
        kb_id = (cfg.get("weknora_knowledge_base_id") or "").strip() or None
        return base, api_key or None, kb_id
    except Exception:
        return None, None, None


def _weknora_search(query: str, top_k: int) -> dict:
    """调用 WeKnora 知识搜索 API，将返回格式统一为与本地 search 一致。"""
    base, api_key, kb_id = _get_weknora_config()
    if not base or not requests:
        return None
    url = base + WEKNORA_SEARCH_PATH
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    payload = {"query": query}
    if kb_id:
        payload["knowledge_base_id"] = kb_id
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=WEKNORA_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {
            "success": False,
            "protocol": "UTCP",
            "message": "WeKnora 请求失败: " + str(e),
            "data": None,
        }
    if not data.get("success") or "data" not in data:
        return {
            "success": False,
            "protocol": "UTCP",
            "message": data.get("message", "WeKnora 返回格式异常"),
            "data": None,
        }
    raw_list = data.get("data") or []
    results = []
    for item in raw_list[:top_k]:
        if isinstance(item, dict):
            content = item.get("content") or ""
            source = item.get("knowledge_filename") or item.get("knowledge_title") or "WeKnora"
            results.append({"source": source, "text": content})
        elif isinstance(item, str):
            results.append({"source": "WeKnora", "text": item})
    return {
        "success": True,
        "protocol": "UTCP",
        "message": "ok",
        "data": {"results": results, "total_chunks": len(raw_list)},
    }


def _chunk_text(text: str, source: str):
    """按双换行或单换行切块，单块不超过 CHUNK_MAX 字符。返回 [{"source": str, "text": str}, ...]"""
    chunks = []
    # 先按双换行分大段
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if len(block) <= CHUNK_MAX:
            chunks.append({"source": source, "text": block})
        else:
            # 再按单换行或句号切
            parts = re.split(r"(?<=[。.\n])\s*", block)
            buf = ""
            for p in parts:
                if len(buf) + len(p) <= CHUNK_MAX:
                    buf += p
                else:
                    if buf:
                        chunks.append({"source": source, "text": buf.strip()})
                    buf = p
            if buf.strip():
                chunks.append({"source": source, "text": buf.strip()})
    return chunks


def load_chunks():
    """扫描 knowledge 目录下所有 .md/.txt，返回所有块列表。"""
    _ensure_dir()
    all_chunks = []
    for ext in ("*.md", "*.txt"):
        for f in KNOWLEDGE_DIR.rglob(ext):
            if not f.is_file():
                continue
            try:
                raw = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            rel = str(f.relative_to(KNOWLEDGE_DIR))
            all_chunks.extend(_chunk_text(raw, rel))
    return all_chunks


def get_status():
    """返回知识库状态：后端类型、目录路径、文件数、块数、文件列表；若启用 WeKnora 则含 weknora_base_url。"""
    weknora_base, _, _ = _get_weknora_config()
    backend = "weknora" if weknora_base else "local"
    _ensure_dir()
    files = []
    total_chunks = 0
    for ext in ("*.md", "*.txt"):
        for f in sorted(KNOWLEDGE_DIR.rglob(ext)):
            if not f.is_file():
                continue
            rel = str(f.relative_to(KNOWLEDGE_DIR))
            try:
                raw = f.read_text(encoding="utf-8", errors="replace")
                n = len(_chunk_text(raw, rel))
            except Exception:
                n = 0
            files.append({"path": rel, "chunks": n})
            total_chunks += n
    out = {
        "backend": backend,
        "dir": str(KNOWLEDGE_DIR),
        "file_count": len(files),
        "chunk_count": total_chunks,
        "files": files,
    }
    if weknora_base:
        out["weknora_base_url"] = weknora_base
    return out


def search(query: str, top_k: int = TOP_K_DEFAULT) -> dict:
    """
    按关键词/语义检索知识库。若已配置 WeKnora 则优先调用 WeKnora 语义检索，否则使用本地 knowledge 目录关键词检索。
    返回 {"success": bool, "message": str, "data": {"results": [{"source", "text"}], "total_chunks": int}}。
    """
    query = (query or "").strip()
    if not query:
        return {
            "success": False,
            "protocol": "UTCP",
            "message": "query 不能为空",
            "data": None,
        }
    weknora_result = _weknora_search(query, top_k)
    if weknora_result is not None:
        return weknora_result
    try:
        chunks = load_chunks()
    except Exception as e:
        return {
            "success": False,
            "protocol": "UTCP",
            "message": str(e),
            "data": None,
        }
    if not chunks:
        return {
            "success": True,
            "protocol": "UTCP",
            "message": "知识库为空，请先在 knowledge 目录下添加 .md 或 .txt 文件",
            "data": {"results": [], "total_chunks": 0},
        }
    # 简单打分：查询词（按空格分）在块中出现的次数，不区分大小写
    q_lower = query.lower()
    q_terms = [t for t in q_lower.split() if len(t) > 1]
    scored = []
    for c in chunks:
        text_lower = (c["text"] or "").lower()
        score = 0
        if q_lower in text_lower:
            score += 10
        for t in q_terms:
            score += text_lower.count(t)
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    results = [x[1] for x in scored[:top_k]]
    return {
        "success": True,
        "protocol": "UTCP",
        "message": "ok",
        "data": {
            "results": results,
            "total_chunks": len(chunks),
        },
    }
