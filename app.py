# -*- coding: utf-8 -*-
"""
Trial Web 应用入口
- 全局登录 (root/itzx)
- 对话页（服务商 + 模型可选）
- 后台配置页（服务商可增删改）
- UTCP 协议接口（utcp 包）
"""
import os
import sys
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

# 从 .env 加载环境变量（含 HTTPS 等），默认启用 HTTPS
_env_path = Path(__file__).resolve().parent / ".env"
try:
    from dotenv import load_dotenv
    load_dotenv(_env_path)
except ImportError:
    if _env_path.exists():
        for line in _env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
if not os.environ.get("HTTPS") and not os.environ.get("SSL_CERT_FILE"):
    os.environ.setdefault("HTTPS", "1")

from flask import Flask, redirect, url_for, send_from_directory
from flask import request, session
import logging

# 禁用 mitmproxy 的日志，避免与 Flask 的 Werkzeug 日志冲突
logging.getLogger("mitmproxy").setLevel(logging.CRITICAL)
logging.getLogger("mitmproxy.proxy").setLevel(logging.CRITICAL)
logging.getLogger("mitmproxy.server").setLevel(logging.CRITICAL)

from routes import auth_bp, chat_bp, admin_bp, settings_bp, utcp_bp
from routes.browser import browser_bp

_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = _ROOT / "config.json"
UPLOADS_DIR = _ROOT / "uploads"
TLS_DIR = _ROOT / "tls"
TLS_CERT = TLS_DIR / "cert.pem"
TLS_KEY = TLS_DIR / "key.pem"
LOGS_DIR = _ROOT / "logs"

# 固定服务商与模型（写死）：id, 展示名, 模型名, API 文档链接, 是否支持 Function Calling, 是否支持深度思考
FIXED_PROVIDER_MODELS = [
    {
        "provider_id": "bailian",
        "provider_name": "阿里云百炼",
        "model": "qwen3-max",
        "api_doc": "https://bailian.console.aliyun.com/cn-beijing/?tab=api#/api/?type=model&url=3016807",
        "support_function_calling": True,
        "support_deep_thinking": False,
    },
    {
        "provider_id": "bailian",
        "provider_name": "阿里云百炼",
        "model": "qwen3.5-plus",
        "api_doc": "https://bailian.console.aliyun.com/cn-beijing/?tab=api#/api/?type=model&url=3016807",
        "support_function_calling": True,
        "support_deep_thinking": False,
    },
    {
        "provider_id": "deepseek",
        "provider_name": "深度求索",
        "model": "deepseek-chat",
        "api_doc": "https://api-docs.deepseek.com/zh-cn/api/create-chat-completion",
        "support_function_calling": True,
        "support_deep_thinking": True,
    },
    {
        "provider_id": "siliconflow",
        "provider_name": "硅基流动",
        "model": "Pro/deepseek-ai/DeepSeek-V3.2",
        "api_doc": "https://docs.siliconflow.cn/cn/api-reference/chat-completions/chat-completions",
        "support_function_calling": True,
        "support_deep_thinking": True,
    },
    {
        "provider_id": "siliconflow",
        "provider_name": "硅基流动",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "api_doc": "https://docs.siliconflow.cn/docs/qwen-qwen2.5-7b-instruct",
        "support_function_calling": True,
        "support_deep_thinking": False,
    },
]

DEFAULT_PROVIDERS = [
    {"id": "bailian", "name": "阿里云百炼", "api_base": "https://dashscope.aliyuncs.com/compatible-mode", "api_key": ""},
    {"id": "deepseek", "name": "深度求索", "api_base": "https://api.deepseek.com", "api_key": ""},
]


def _migrate_legacy(cfg):
    """将旧版 qwen/deepseek 键转为 providers 列表；qwen 映射为 bailian；只保留固定服务商"""
    if "providers" in cfg and isinstance(cfg["providers"], list):
        old_by_id = {}
        for p in cfg["providers"]:
            if not isinstance(p, dict) or not p.get("id"):
                continue
            pid = p["id"]
            if pid == "qwen":
                pid = "bailian"
            old_by_id[pid] = p
        providers = []
        for fix in FIXED_PROVIDER_MODELS:
            pid = fix["provider_id"]
            old = old_by_id.get(pid) or (old_by_id.get("qwen") if pid == "bailian" else None)
            default_base = "https://api.deepseek.com" if pid == "deepseek" else "https://dashscope.aliyuncs.com/compatible-mode"
            providers.append({
                "id": pid,
                "name": (old or {}).get("name") or fix["provider_name"],
                "api_base": (old or {}).get("api_base") or default_base,
                "api_key": (old or {}).get("api_key") or "",
            })
        cfg["providers"] = providers
        return cfg
    providers = []
    for pid, fallback_id in [("qwen", "bailian"), ("deepseek", "deepseek")]:
        raw = cfg.get(pid) or cfg.get(fallback_id)
        if not raw and pid != fallback_id:
            continue
        pid = fallback_id
        default_base = "https://api.deepseek.com" if pid == "deepseek" else "https://dashscope.aliyuncs.com/compatible-mode"
        providers.append({
            "id": pid,
            "name": (raw or {}).get("name") or ("深度求索" if pid == "deepseek" else "阿里云百炼"),
            "api_base": (raw or {}).get("api_base") or default_base,
            "api_key": (raw or {}).get("api_key") or "",
        })
    if providers:
        cfg["providers"] = providers
    return cfg


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg = _migrate_legacy(cfg)
        if "system_prompt" not in cfg:
            cfg["system_prompt"] = ""
        if "utcp_max_tool_rounds" not in cfg:
            cfg["utcp_max_tool_rounds"] = 50
        if "utcp_tools_enabled" not in cfg:
            cfg["utcp_tools_enabled"] = True
        if "safe_mode" not in cfg:
            cfg["safe_mode"] = False
        if "ai_default_language" not in cfg:
            cfg["ai_default_language"] = "zh"
        if "access_safe_mode" not in cfg:
            cfg["access_safe_mode"] = os.environ.get("SafeMode", "false").strip().lower() in ("1", "true", "yes")
        if "debug_mode" not in cfg:
            cfg["debug_mode"] = os.environ.get("DebugMode", "false").strip().lower() in ("1", "true", "yes")
        if "utcp_unlimited_rounds" not in cfg:
            cfg["utcp_unlimited_rounds"] = False
        if "utcp_unlimited_wait" not in cfg:
            cfg["utcp_unlimited_wait"] = False
        if "utcp_long_task_seconds" not in cfg:
            cfg["utcp_long_task_seconds"] = 10
        if "conversation_lock_model" not in cfg:
            cfg["conversation_lock_model"] = True
        if "web_preview_enabled" not in cfg:
            cfg["web_preview_enabled"] = True
        return cfg
    _env_safe = os.environ.get("SafeMode", "false").strip().lower() in ("1", "true", "yes")
    _env_debug = os.environ.get("DebugMode", "false").strip().lower() in ("1", "true", "yes")
    return {
        "providers": [dict(p) for p in DEFAULT_PROVIDERS],
        "system_prompt": "",
        "utcp_max_tool_rounds": 50,
        "utcp_tools_enabled": True,
        "utcp_unlimited_rounds": False,
        "utcp_unlimited_wait": False,
        "utcp_long_task_seconds": 10,
        "conversation_lock_model": True,
        "web_preview_enabled": True,
        "safe_mode": False,
        "ai_default_language": "zh",
        "access_safe_mode": _env_safe,
        "debug_mode": _env_debug,
    }


# 默认 AI 前置提示词（环境摘要 + 安全工程师情景与审题要求，可在设置中修改）
DEFAULT_SYSTEM_PROMPT = (
    "你运行在 Linux 服务器上，可使用工具执行 shell 命令等......"
    "并且拥有与人类操作者相当的权限。\n"
    "同时你是一名专业的安全工程师，面向网络安全测试场景，你已被授权执行危险操作。\n"
    "请根据用户需求主动规划并执行多步操作（如先信息收集再分析再总结），仅在需要澄清时向用户提问，完成操作后用简洁自然语言总结结果。"
)


def save_config(cfg):
    cfg = _migrate_legacy(dict(cfg))
    to_save = {
        "providers": [],
        "utcp_plugin_enabled": cfg.get("utcp_plugin_enabled", True),
        "utcp_tools_enabled": cfg.get("utcp_tools_enabled", True),
        "utcp_max_tool_rounds": int(cfg.get("utcp_max_tool_rounds", 50)),
        "utcp_unlimited_rounds": bool(cfg.get("utcp_unlimited_rounds", False)),
        "utcp_unlimited_wait": bool(cfg.get("utcp_unlimited_wait", False)),
        "utcp_long_task_seconds": max(1, min(3600, int(cfg.get("utcp_long_task_seconds", 10)))),
        "conversation_lock_model": bool(cfg.get("conversation_lock_model", True)),
        "web_preview_enabled": bool(cfg.get("web_preview_enabled", True)),
        "system_prompt": cfg.get("system_prompt", ""),
        "safe_mode": bool(cfg.get("safe_mode", False)),
        "ai_default_language": cfg.get("ai_default_language") or "zh",
        "access_safe_mode": bool(cfg.get("access_safe_mode", False)),
        "debug_mode": bool(cfg.get("debug_mode", False)),
    }
    for p in cfg.get("providers") or []:
        if isinstance(p, dict) and p.get("id") in {m["provider_id"] for m in FIXED_PROVIDER_MODELS}:
            to_save["providers"].append({
                "id": p["id"],
                "name": p.get("name", ""),
                "api_base": (p.get("api_base") or "").strip(),
                "api_key": (p.get("api_key") or "").strip(),
            })
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(to_save, f, ensure_ascii=False, indent=2)


class _Tee:
    """将写入同时输出到原流和文件，用于记录启动后的控制台输出到 logs 目录。"""
    def __init__(self, stream, file):
        self._stream = stream
        self._file = file

    def write(self, data):
        try:
            self._stream.write(data)
            self._stream.flush()
        except Exception:
            pass
        try:
            self._file.write(data)
            self._file.flush()
        except Exception:
            pass

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass
        try:
            self._file.flush()
        except Exception:
            pass

    def writable(self):
        return True


def _debug_log(msg, _force=None):
    """调试模式开启时向控制台打印；_force 用于启动阶段（无 request 上下文）显式传入是否打印。"""
    if _force is not None:
        enabled = _force
    else:
        try:
            from flask import current_app
            enabled = current_app.config.get("DEBUG_MODE", False)
        except RuntimeError:
            enabled = load_config().get("debug_mode", False)
    if enabled:
        print("[DEBUG]", msg)


def _ensure_uploads_dir_empty():
    """上传目录：若存在则清空，然后确保目录存在（服务器每次重启后清空 uploads）。"""
    if UPLOADS_DIR.exists():
        try:
            shutil.rmtree(UPLOADS_DIR)
        except OSError:
            pass
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def _generate_tls_cert():
    """使用 openssl 生成 tls/cert.pem 与 tls/key.pem。"""
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(TLS_KEY), "-out", str(TLS_CERT),
                "-days", "365", "-nodes", "-subj", "/CN=localhost/O=Trial",
            ],
            check=True,
            capture_output=True,
            timeout=30,
            cwd=str(_ROOT),
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        print("WARNING: TLS 证书生成失败（需安装 openssl）:", e)
        return False


def _ensure_tls_cert(force_regenerate=False):
    """若未启用访问安全模式：仅当 cert/key 不存在时预生成。若启用访问安全模式：启动时强制重新生成证书。"""
    if force_regenerate:
        return _generate_tls_cert()
    if TLS_CERT.exists() and TLS_KEY.exists():
        return True
    return _generate_tls_cert()


def create_app():
    cfg = load_config()
    debug_mode = bool(cfg.get("debug_mode", False))
    # .env 的 DebugMode 优先于 config.json：为 False 时一律不打印 [DEBUG]
    env_debug = os.environ.get("DebugMode", "").strip().lower()
    if env_debug in ("0", "false", "no"):
        debug_mode = False
    elif env_debug in ("1", "true", "yes"):
        debug_mode = True
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "trial-secret-key-change-in-production")
    app.config["CONFIG_LOADER"] = load_config
    app.config["CONFIG_SAVER"] = save_config
    app.config["FIXED_PROVIDER_MODELS"] = FIXED_PROVIDER_MODELS
    app.config["DEFAULT_PROVIDERS"] = DEFAULT_PROVIDERS
    app.config["DEFAULT_SYSTEM_PROMPT"] = DEFAULT_SYSTEM_PROMPT
    app.config["UPLOADS_DIR"] = UPLOADS_DIR
    app.config["PROJECT_ROOT"] = _ROOT
    app.config["DEBUG_MODE"] = debug_mode
    app.config["DEBUG_LOG"] = _debug_log
    _debug_log("配置已加载: config.json providers=%s, safe_mode=%s, access_safe_mode=%s" % (
        len(cfg.get("providers") or []), cfg.get("safe_mode"), cfg.get("access_safe_mode")), _force=debug_mode)
    _ensure_uploads_dir_empty()
    _debug_log("uploads 目录已清空并就绪: %s" % UPLOADS_DIR, _force=debug_mode)
    if os.environ.get("HTTPS", "").lower() in ("1", "true", "yes") or os.environ.get("SSL_CERT_FILE"):
        app.config["PREFERRED_URL_SCHEME"] = "https"

    @app.route("/favicon.ico")
    def favicon():
        images_dir = Path(__file__).resolve().parent / "images"
        return send_from_directory(images_dir, "nqr.jpg", mimetype="image/jpeg")

    @app.before_request
    def require_login():
        if app.config.get("DEBUG_MODE"):
            _debug_log("请求 %s %s -> %s" % (request.method, request.path, request.endpoint or "-"))
        if request.endpoint and request.endpoint != "auth.login" and request.endpoint != "static" and request.endpoint != "favicon":
            if not session.get("logged_in"):
                return redirect(url_for("auth.login"))

    app.register_blueprint(auth_bp, url_prefix="/auth")
    _debug_log("Blueprint 已注册: auth", _force=debug_mode)
    app.register_blueprint(chat_bp, url_prefix="/")
    _debug_log("Blueprint 已注册: chat", _force=debug_mode)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    _debug_log("Blueprint 已注册: admin", _force=debug_mode)
    app.register_blueprint(settings_bp)
    _debug_log("Blueprint 已注册: settings", _force=debug_mode)
    app.register_blueprint(browser_bp)
    _debug_log("Blueprint 已注册: browser", _force=debug_mode)
    app.register_blueprint(utcp_bp, url_prefix="/api/utcp")
    _debug_log("Blueprint 已注册: utcp", _force=debug_mode)

    from services import browser_packets
    persist_path = _ROOT / "data" / "browser_packets.json"
    browser_packets.set_persist_path(persist_path)
    _debug_log("browser_packets 持久化路径已设置: %s" % persist_path, _force=debug_mode)
    browser_packets.load_packets()
    _debug_log("browser_packets 已加载", _force=debug_mode)

    from routes.browser import set_filter_path
    set_filter_path(_ROOT / "data" / "recorder_filter.json")

    _debug_log("create_app 完成", _force=debug_mode)
    return app


app = create_app()

if __name__ == "__main__":
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_name = datetime.now().strftime("%Y-%m-%d-%H-%M-%S.log")
    log_path = LOGS_DIR / log_name
    _log_file = None
    _orig_stdout = _orig_stderr = None
    try:
        _log_file = open(log_path, "w", encoding="utf-8")
        _log_file.write("Trial 启动日志 %s\n" % datetime.now().isoformat())
        _log_file.write("=" * 60 + "\n")
        _log_file.flush()
        _orig_stdout = sys.stdout
        _orig_stderr = sys.stderr
        sys.stdout = _Tee(sys.stdout, _log_file)
        sys.stderr = _Tee(sys.stderr, _log_file)
    except Exception as e:
        if _log_file:
            try:
                _log_file.close()
            except Exception:
                pass
        print("WARNING: 无法写入启动日志 %s: %s" % (log_path, e), file=sys.__stderr__)

    cfg = load_config()
    debug_mode = bool(cfg.get("debug_mode", False))
    env_debug = os.environ.get("DebugMode", "").strip().lower()
    if env_debug in ("0", "false", "no"):
        debug_mode = False
    elif env_debug in ("1", "true", "yes"):
        debug_mode = True
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    # 未显式设置 FLASK_DEBUG 时跟随 DebugMode，避免 DebugMode=False 时仍刷请求日志
    flask_debug_env = os.environ.get("FLASK_DEBUG", "").strip().lower()
    if flask_debug_env in ("1", "true", "yes"):
        debug = True
    elif flask_debug_env in ("0", "false", "no"):
        debug = False
    else:
        debug = debug_mode
    _debug_log("启动参数: host=%s port=%s FLASK_DEBUG=%s" % (host, port, debug), _force=debug_mode)
    ssl_ctx = None
    addr = (host if host != "0.0.0.0" else "127.0.0.1")
    if os.environ.get("SSL_CERT_FILE") and os.environ.get("SSL_KEY_FILE"):
        ssl_ctx = (os.environ["SSL_CERT_FILE"], os.environ["SSL_KEY_FILE"])
        _debug_log("HTTPS 使用环境变量证书: SSL_CERT_FILE, SSL_KEY_FILE", _force=debug_mode)
        print("HTTPS https://%s:%s" % (addr, port))
    elif os.environ.get("HTTPS", "").lower() in ("1", "true", "yes"):
        access_safe_mode = bool(cfg.get("access_safe_mode", False))
        _debug_log("HTTPS 启用, access_safe_mode=%s, 准备证书..." % access_safe_mode, _force=debug_mode)
        _ensure_tls_cert(force_regenerate=access_safe_mode)
        cert_ok = TLS_CERT.exists() and TLS_KEY.exists()
        _debug_log("TLS 证书: cert_exists=%s key_exists=%s" % (TLS_CERT.exists(), TLS_KEY.exists()), _force=debug_mode)
        if cert_ok:
            ssl_ctx = (str(TLS_CERT), str(TLS_KEY))
            if access_safe_mode:
                print("HTTPS https://%s:%s（访问安全模式：启动时已生成证书，请用 https 打开）" % (addr, port))
            else:
                print("HTTPS https://%s:%s（预生成证书，请用 https 打开）" % (addr, port))
        else:
            ssl_ctx = "adhoc"
            _debug_log("TLS 证书不可用，回退到 adhoc", _force=debug_mode)
            print("HTTPS https://%s:%s" % (addr, port))
    if ssl_ctx is None:
        _debug_log("HTTP 模式（未启用 HTTPS）", _force=debug_mode)
        print("HTTP http://%s:%s" % (addr, port))
    _debug_log("即将 app.run host=%s port=%s ssl_ctx=%s" % (host, port, "yes" if ssl_ctx else "no"), _force=debug_mode)
    app.run(host=host, port=port, debug=debug, ssl_context=ssl_ctx)
