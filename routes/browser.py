# -*- coding: utf-8 -*-
"""记录器：录制代理端口展示、录包列表与详情。无内置浏览器，需在本地浏览器中配置代理后使用。"""
import json
from pathlib import Path

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, send_file, current_app

from services import browser_packets
from services import browser_session

_FILTER_PATH = None  # 由应用设置，如 Path("data/recorder_filter.json")


def set_filter_path(path):
    global _FILTER_PATH
    _FILTER_PATH = path


def _get_filter_path():
    if _FILTER_PATH is not None:
        return _FILTER_PATH
    try:
        root = Path(current_app.root_path)
        return root / "data" / "recorder_filter.json"
    except Exception:
        return None


def _load_recorder_filter():
    p = _get_filter_path()
    if not p:
        return {"enabled": False, "addresses": []}
    try:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            return {
                "enabled": bool(d.get("enabled")),
                "addresses": [str(x) for x in d.get("addresses", []) if x],
            }
    except Exception:
        pass
    return {"enabled": False, "addresses": []}


def _save_recorder_filter(data):
    p = _get_filter_path()
    if not p:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

browser_bp = Blueprint("browser", __name__, url_prefix="/")


def _browser_debug(msg):
    log = current_app.config.get("DEBUG_LOG") if current_app else None
    if callable(log):
        log(msg)


@browser_bp.route("browser")
def index():
    """原内置浏览器入口，重定向到记录器。"""
    return redirect(url_for("browser.recorder"))


@browser_bp.route("recorder")
def recorder():
    """记录器页面：显示代理端口与 Wireshark 式数据包列表。"""
    return render_template("recorder.html")


@browser_bp.route("api/recorder/proxy", methods=["GET"])
def api_proxy():
    """确保录制代理已启动并返回代理地址与端口，供用户在浏览器中配置使用。"""
    ok, err = browser_session.ensure_proxy_started()
    if not ok:
        _browser_debug("录制代理启动失败: %s" % (err or "未知错误"))
        return jsonify({"ok": False, "error": err}), 500
    url = browser_session.get_proxy_url()
    port = browser_session.get_proxy_port()
    host = "127.0.0.1"
    _browser_debug("录制代理已就绪: %s port=%s" % (url, port))
    return jsonify({
        "ok": True,
        "proxy_url": url,
        "host": host,
        "port": port,
    })


@browser_bp.route("api/recorder/filter", methods=["GET"])
def recorder_filter_get():
    """返回记录器过滤器配置：enabled, addresses。"""
    return jsonify(_load_recorder_filter())


@browser_bp.route("api/recorder/filter", methods=["POST"])
def recorder_filter_post():
    """更新过滤器：body 可含 enabled(bool)、add(str)、remove(str)。"""
    data = _load_recorder_filter()
    body = request.get_json(silent=True) or {}
    if "enabled" in body:
        data["enabled"] = bool(body["enabled"])
    if "add" in body and body["add"]:
        addr = str(body["add"]).strip()
        if addr and addr not in data["addresses"]:
            data["addresses"].append(addr)
    if "remove" in body:
        val = str(body["remove"]).strip()
        if val in data["addresses"]:
            data["addresses"].remove(val)
    _save_recorder_filter(data)
    return jsonify(data)


@browser_bp.route("api/browser/packets", methods=["GET", "POST"])
def packets_list_or_clear():
    """GET：返回录包列表（可选 url_contains, url_contains_any, limit）；POST：清空录包。"""
    if request.method == "POST":
        browser_packets.clear_packets()
        _browser_debug("录包已清空")
        return jsonify({"ok": True, "message": "已清空"})
    url_contains = request.args.get("url_contains") or ""
    limit = request.args.get("limit", type=int) or 200
    url_contains_any = request.args.getlist("url_contains_any") or request.args.get("url_contains_any")
    if isinstance(url_contains_any, str) and url_contains_any.strip():
        try:
            url_contains_any = json.loads(url_contains_any)
        except Exception:
            url_contains_any = [s.strip() for s in url_contains_any.split(",") if s.strip()]
    if not isinstance(url_contains_any, list):
        url_contains_any = []
    items = browser_packets.list_packets(
        url_contains=url_contains if not url_contains_any else None,
        url_contains_any=url_contains_any if url_contains_any else None,
        limit=limit,
    )
    _browser_debug("录包列表: count=%s limit=%s" % (len(items), limit))
    return jsonify({"packets": items})


@browser_bp.route("api/browser/packets/<packet_id>", methods=["GET"])
def packet_detail(packet_id):
    """返回单条录包详情。"""
    p = browser_packets.get_packet(packet_id)
    if not p:
        return jsonify({"error": "未找到"}), 404
    return jsonify(p)


@browser_bp.route("api/recorder/cert", methods=["GET"])
def download_cert():
    """
    下载 Mitmproxy CA 证书
    用于解密 HTTPS 流量，用户需要将此证书安装到浏览器/系统中并设置为"始终信任"
    """
    cert_path = browser_session.get_mitmproxy_cert_path()
    if not cert_path:
        return jsonify({"error": "证书文件未找到。请先启动代理以生成证书。"}), 404
    
    try:
        return send_file(
            cert_path, 
            as_attachment=True, 
            download_name="mitmproxy-ca-cert.pem",
            mimetype="application/x-pem-file"
        )
    except Exception as e:
        return jsonify({"error": f"证书下载失败: {str(e)}"}), 500
