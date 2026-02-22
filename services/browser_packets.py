# -*- coding: utf-8 -*-
"""记录器流量录包存储：供录制代理与记录器页、AI 工具共用。"""
import json
import time
import uuid
from pathlib import Path

_PACKETS = []
_MAX_BODY_PREVIEW = 64 * 1024  # 单次请求/响应 body 预览最多 64KB
_PERSIST_PATH = None  # 由应用设置，如 Path("data/browser_packets.json")


def set_persist_path(path):
    global _PERSIST_PATH
    _PERSIST_PATH = path


def _truncate(s, max_len=_MAX_BODY_PREVIEW):
    if s is None:
        return None
    if isinstance(s, bytes):
        s = s.decode("utf-8", errors="replace")
    s = str(s)
    return s[:max_len] + ("…" if len(s) > max_len else "")


def add_packet(method: str, url: str, request_headers: dict, request_body, response_status: int, response_headers: dict, response_body):
    """记录一条请求/响应。body 可为 str 或 bytes，会做截断预览。"""
    pid = str(uuid.uuid4())[:8]
    ts = time.time()
    req_h = dict(request_headers) if request_headers else {}
    req_body = _truncate(request_body)
    res_h = dict(response_headers) if response_headers else {}
    res_body = _truncate(response_body)
    entry = {
        "id": pid,
        "time": ts,
        "method": (method or "GET").upper(),
        "url": url or "",
        "request_headers": req_h,
        "request_body_preview": req_body,
        "response_status": response_status,
        "response_headers": res_h,
        "response_body_preview": res_body,
    }
    _PACKETS.append(entry)
    _persist()
    return pid


def list_packets(url_contains: str = None, url_contains_any: list = None, limit: int = 200):
    """返回录包列表，可选按 URL 过滤（单个或任意多个匹配），按时间倒序，最多 limit 条。"""
    out = list(_PACKETS)
    if url_contains_any and len(url_contains_any) > 0:
        patterns = [str(s).strip().lower() for s in url_contains_any if s and str(s).strip()]
        if patterns:
            def match_any(url):
                u = (url or "").lower()
                return any(q in u for q in patterns)
            out = [p for p in out if match_any(p.get("url") or "")]
    elif url_contains and url_contains.strip():
        q = url_contains.strip().lower()
        out = [p for p in out if q in (p.get("url") or "").lower()]
    out.reverse()
    return out[: max(1, min(1000, int(limit) if limit else 200))]


def get_packet(packet_id: str):
    """按 id 返回一条录包，不存在返回 None。"""
    for p in _PACKETS:
        if p.get("id") == packet_id:
            return p
    return None


def clear_packets():
    """清空所有录包。"""
    global _PACKETS
    _PACKETS = []
    _persist()


def _persist():
    if not _PERSIST_PATH:
        return
    try:
        p = Path(_PERSIST_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(_PACKETS, f, ensure_ascii=False, indent=0)
    except Exception:
        pass


def load_packets():
    """从文件加载录包（应用启动时调用）。"""
    global _PACKETS
    if not _PERSIST_PATH:
        return
    try:
        p = Path(_PERSIST_PATH)
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                _PACKETS = json.load(f)
            if not isinstance(_PACKETS, list):
                _PACKETS = []
    except Exception:
        _PACKETS = []
