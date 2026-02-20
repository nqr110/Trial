# -*- coding: utf-8 -*-
"""录制代理单例：供记录器使用，将浏览器流量录包。无内置浏览器，仅暴露代理端口供本机浏览器配置使用。"""
import threading

_proxy = None
_lock = threading.Lock()


def ensure_proxy_started():
    """启动录制代理（若未启动）。返回 (True, None) 或 (False, error_message)。"""
    global _proxy
    with _lock:
        if _proxy is not None:
            try:
                if _proxy.proxy_url:
                    return (True, None)
            except Exception:
                pass
            try:
                _proxy.stop()
            except Exception:
                pass
            _proxy = None
        try:
            from services.recording_proxy import RecordingProxyServer
            _proxy = RecordingProxyServer("127.0.0.1", 0)
            _proxy.start()
            if _proxy.proxy_url:
                return (True, None)
            return (False, "代理启动失败")
        except Exception as e:
            _proxy = None
            return (False, str(e))


def get_proxy_url():
    """当前录制代理地址，如 http://127.0.0.1:xxxx；未启动时返回 None。"""
    with _lock:
        if _proxy is None:
            return None
        try:
            return _proxy.proxy_url
        except Exception:
            return None


def get_proxy_port():
    """当前录制代理端口号；未启动时返回 None。"""
    url = get_proxy_url()
    if not url or not url.startswith("http://"):
        return None
    try:
        part = url.rstrip("/").split(":")[-1]
        return int(part)
    except (ValueError, IndexError):
        return None


def stop_proxy():
    """停止录制代理。"""
    global _proxy
    with _lock:
        p, _proxy = _proxy, None
        if p:
            try:
                p.stop()
            except Exception:
                pass
