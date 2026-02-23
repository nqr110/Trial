# -*- coding: utf-8 -*-
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests
from flask import Blueprint, render_template, request, jsonify, Response, stream_with_context, redirect, url_for, current_app
from services.llm import get_available_models, chat_completion, chat_completion_with_tools, chat_completion_stream, summarize_conversation_title


def _chat_debug(msg):
    log = current_app.config.get("DEBUG_LOG") if current_app else None
    if callable(log):
        log(msg)


def _safe_filename(name):
    """保留扩展名，文件名仅保留安全字符。"""
    name = name or "file"
    if "/" in name or "\\" in name:
        name = name.replace("\\", "/").split("/")[-1]
    safe = re.sub(r"[^\w.\-]", "_", name)
    return safe or "file"


def _inject_attachment_paths(messages, attachment_paths):
    """若有上传文件路径，在最后一条用户消息前注入说明，便于模型用 read_file 读取。"""
    if not attachment_paths or not messages:
        return messages
    paths = [p for p in attachment_paths if p and isinstance(p, str)]
    if not paths:
        return messages
    prefix = "【用户在本轮上传了以下文件，路径相对于项目根，可使用 read_file 工具读取】\n" + "\n".join(paths) + "\n\n"
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user":
            out[i] = dict(out[i])
            out[i]["content"] = prefix + (out[i].get("content") or "")
            break
    return out


def _model_label(provider_id, model):
    """根据 provider_id 与 model 返回展示用「服务商 - 模型」"""
    for m in get_available_models():
        if m.get("provider_id") == provider_id and m.get("model") == model:
            return m.get("label") or f"{provider_id} - {model}"
    return f"{provider_id} - {model}"
from services.conversation_store import (
    list_conversations,
    get_conversation,
    create_conversation,
    update_conversation,
    delete_conversation,
)
import json

chat_bp = Blueprint("chat", __name__)


def _web_preview_proxy_allowed_host(url_str: str) -> bool:
    """仅允许 localhost/127.0.0.1 或当前请求 host，避免代理任意外网站。"""
    try:
        parsed = urlparse(url_str)
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.netloc or "").split(":")[0].lower()
        if host in ("127.0.0.1", "localhost", "::1"):
            return True
        req_host = (request.host or "").split(":")[0].lower()
        if host == req_host:
            return True
        return False
    except Exception:
        return False


@chat_bp.route("/api/web-preview-proxy", methods=["GET"])
def web_preview_proxy():
    """
    代理 Web 预览 iframe 的请求：后端拉取目标 URL，去掉禁止嵌入的响应头并注入 <base>，
    使内置预览能显示本地或同源的自动化页面（避免 X-Frame-Options、混合内容导致空白）。
    """
    url_str = (request.args.get("url") or "").strip()
    if not url_str.startswith("http://") and not url_str.startswith("https://"):
        return Response("url 须为 http 或 https", status=400)
    if not _web_preview_proxy_allowed_host(url_str):
        return Response("仅支持 localhost / 127.0.0.1 或当前站点", status=403)
    try:
        r = requests.get(url_str, timeout=15, stream=False, headers={"User-Agent": "Trial-WebPreview/1.0"})
        r.raise_for_status()
        body = r.content
        content_type = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type == "text/html" and body:
            try:
                text = body.decode("utf-8", errors="replace")
                base_url = url_str if url_str.endswith("/") else (url_str.rsplit("/", 1)[0] + "/")
                base_tag = '<base href="' + base_url.replace('"', "&quot;") + '">'
                head_match = re.search(r"<head[^>]*>", text, re.IGNORECASE)
                if head_match:
                    text = text[: head_match.end()] + base_tag + text[head_match.end() :]
                else:
                    text = base_tag + text
                body = text.encode("utf-8")
            except Exception:
                pass
        resp = Response(body, status=200)
        resp.headers["Content-Type"] = r.headers.get("Content-Type") or "text/html; charset=utf-8"
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except requests.RequestException as e:
        return Response("代理请求失败: " + str(e), status=502)


@chat_bp.route("/utcp")
def utcp_console():
    """旧路径：重定向到设置下的 UTCP 控制台"""
    return redirect(url_for("settings.utcp"))


@chat_bp.route("/")
def index():
    """对话页：先选服务商再选模型，历史对话，流式输出"""
    providers = get_available_models()
    return render_template("chat.html", providers=providers)


@chat_bp.route("/api/models", methods=["GET"])
def api_models():
    """获取可用模型列表及全局配置（UTCP 插件、自动化工作流开关、最大轮次）"""
    models = get_available_models()
    cfg = current_app.config["CONFIG_LOADER"]()
    max_rounds = int(cfg.get("utcp_max_tool_rounds", 50))
    if cfg.get("utcp_unlimited_rounds"):
        max_rounds = 9999
    return jsonify({
        "providers": models,
        "utcp_plugin_enabled": cfg.get("utcp_plugin_enabled", True),
        "utcp_tools_enabled": cfg.get("utcp_tools_enabled", True),
        "utcp_max_tool_rounds": max_rounds,
        "utcp_unlimited_rounds": bool(cfg.get("utcp_unlimited_rounds", False)),
        "utcp_unlimited_wait": bool(cfg.get("utcp_unlimited_wait", False)),
        "utcp_long_task_seconds": max(1, min(3600, int(cfg.get("utcp_long_task_seconds", 10)))),
        "conversation_lock_model": bool(cfg.get("conversation_lock_model", True)),
        "web_preview_enabled": bool(cfg.get("web_preview_enabled", True)),
    })


@chat_bp.route("/api/conversations", methods=["GET"])
def api_conversations_list():
    """历史对话列表"""
    items = list_conversations()
    return jsonify({"conversations": items})


@chat_bp.route("/api/conversations", methods=["POST"])
def api_conversations_create():
    """新建对话"""
    data = request.get_json() or {}
    title = data.get("title") or "新对话"
    conv = create_conversation(title=title)
    return jsonify(conv)


@chat_bp.route("/api/conversations/<cid>", methods=["GET"])
def api_conversation_get(cid):
    """获取单条对话（含 messages）"""
    conv = get_conversation(cid)
    if not conv:
        return jsonify({"error": "对话不存在"}), 404
    return jsonify(conv)


@chat_bp.route("/api/conversations/<cid>", methods=["PATCH"])
def api_conversation_update(cid):
    """更新对话 title 或 messages"""
    data = request.get_json() or {}
    conv = update_conversation(
        cid,
        title=data.get("title"),
        messages=data.get("messages"),
    )
    if not conv:
        return jsonify({"error": "对话不存在"}), 404
    return jsonify(conv)


@chat_bp.route("/api/conversations/<cid>/messages", methods=["PATCH"])
def api_conversation_messages_patch(cid):
    """删除某一对话轮次。body: { "remove_turn_index": 0 }，轮次为 user+assistant 对，0 表示第一轮。"""
    conv = get_conversation(cid)
    if not conv:
        return jsonify({"error": "对话不存在"}), 404
    data = request.get_json() or {}
    try:
        i = int(data.get("remove_turn_index", -1))
    except (TypeError, ValueError):
        return jsonify({"error": "缺少或无效的 remove_turn_index"}), 400
    msgs = conv.get("messages") or []
    n = len(msgs) // 2
    if i < 0 or i >= n:
        return jsonify({"error": "轮次下标越界"}), 400
    new_msgs = msgs[: 2 * i] + msgs[2 * i + 2 :]
    update_conversation(cid, messages=new_msgs)
    updated = get_conversation(cid)
    return jsonify(updated)


@chat_bp.route("/api/conversations/<cid>", methods=["DELETE"])
def api_conversation_delete(cid):
    """删除一条历史对话"""
    if not get_conversation(cid):
        return jsonify({"error": "对话不存在"}), 404
    delete_conversation(cid)
    return jsonify({"ok": True})


@chat_bp.route("/api/upload", methods=["POST"])
def api_upload():
    """上传文件到 uploads 目录，文件类型不限制。返回相对项目根的路径列表。"""
    uploads_dir = Path(current_app.config["UPLOADS_DIR"])
    uploads_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    files = request.files.getlist("files") or request.files.getlist("file")
    if not files and request.files.get("file"):
        files = [request.files.get("file")]
    for f in files:
        if not f or not f.filename:
            continue
        name = str(uuid.uuid4()) + "_" + (_safe_filename(f.filename) or "file")
        dest = uploads_dir / name
        try:
            f.save(str(dest))
            paths.append("uploads/" + name)
        except Exception as e:
            return jsonify({"error": "保存失败: " + str(e), "paths": paths}), 500
    return jsonify({"paths": paths})


def _load_prompt_module(key):
    """从 prompts/<key>.txt 读取内容，key 仅允许字母数字下划线，文件不存在返回空字符串。"""
    if not key or not re.match(r"^[a-zA-Z0-9_]+$", str(key)):
        return ""
    root = current_app.config.get("PROJECT_ROOT")
    if not root:
        return ""
    path = Path(root) / "prompts" / f"{key}.txt"
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        pass
    return ""


def _apply_weknora_memory_if_enabled(messages, cfg):
    """
    若启用 WeKnora 对话记忆：仅用 WeKnora 检索结果作为上下文，送入模型的为 system + 一条 user（记忆 + 当前问题）。
    不再使用本地滑动窗口。
    """
    if not cfg.get("weknora_memory_enabled") or not (cfg.get("weknora_base_url") or "").strip() or not (cfg.get("weknora_memory_kb_id") or "").strip():
        return messages
    try:
        from services.weknora_memory import retrieve_memory, format_memory_for_prompt
    except ImportError:
        return messages
    head = []
    rest = list(messages)
    if rest and rest[0].get("role") == "system":
        head = [rest[0]]
        rest = rest[1:]
    last_user_content = ""
    for m in reversed(rest):
        if m.get("role") == "user":
            last_user_content = (m.get("content") or "").strip()
            break
    if not last_user_content:
        return messages
    memory_chunks = retrieve_memory(last_user_content)
    memory_text = format_memory_for_prompt(memory_chunks)
    if memory_text:
        user_content = memory_text + "\n\n---\n\n" + last_user_content
    else:
        user_content = last_user_content
    return head + [{"role": "user", "content": user_content}]


# 需求判断：仅询问进度/状态/总结时视为「对话分析」，本回合不调用工具
_PROGRESS_STATUS_PATTERNS = (
    r"当前进度",
    r"进度\s*怎么样",
    r"做到\s*哪",
    r"到\s*哪\s*一步",
    r"先说说\s*现在",
    r"现在\s*什么\s*情况",
    r"情况\s*怎么样",
    r"总结\s*一下",
    r"汇报\s*一下",
    r"说说\s*进展",
    r"进展\s*如何",
    r"到哪了",
    r"怎么样了\s*$",
    r"现在\s*怎样\s*了",
)


def _is_progress_or_status_query(user_content: str) -> bool:
    """
    判断当前用户消息是否仅为「询问进度/状态/总结」（对话分析），
    若是则本回合应只做自然语言回答、不执行自动化工具。
    """
    if not user_content or not isinstance(user_content, str):
        return False
    text = user_content.strip()
    if len(text) > 200:
        return False
    for pat in _PROGRESS_STATUS_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return True
    if re.search(r"^(现在|当前|目前|刚才).{0,20}(情况|进度|进展|状态|结果)", text):
        return True
    if re.search(r"(情况|进度|进展|状态)\s*(如何|怎样|怎么样|如何了)\s*[？?]?\s*$", text):
        return True
    return False


def _inject_system_prompt(messages, use_utcp_tools, request_data=None):
    """若配置了前置提示词（或启用 UTCP 时使用默认），则在 messages 前插入 system 消息；可追加动态模块并附加语言约束。"""
    cfg = current_app.config["CONFIG_LOADER"]()
    system_prompt = (cfg.get("system_prompt") or "").strip()
    if use_utcp_tools and not system_prompt:
        system_prompt = (current_app.config.get("DEFAULT_SYSTEM_PROMPT") or "").strip()
    if use_utcp_tools and system_prompt:
        system_prompt = (
            system_prompt
            + "\n\n【需求区分】当用户仅询问当前进度、状态或总结时（如「当前进度怎么样了？」「做到哪了？」），"
            "只根据已有对话与历史工具结果用自然语言回答，本回合不要调用任何工具。"
            "当认为任务已经完成时，请先简要分析再给出最终报告，可在开头使用【任务完成】便于用户识别。"
        ).strip()
    # 动态提示词模块：优先请求体 prompt_modules，否则用配置 system_prompt_modules
    modules = []
    if request_data and isinstance(request_data.get("prompt_modules"), list):
        modules = [m for m in request_data["prompt_modules"] if isinstance(m, str)]
    elif not modules and isinstance(cfg.get("system_prompt_modules"), list):
        modules = [m for m in cfg["system_prompt_modules"] if isinstance(m, str)]
    for key in modules:
        part = _load_prompt_module(key)
        if part:
            system_prompt = (system_prompt + "\n\n" + part).strip()
    lang = (cfg.get("ai_default_language") or "zh").strip() or "zh"
    if lang == "zh":
        system_prompt = (system_prompt + "\n\n请始终使用中文回复。").strip()
    elif lang == "en":
        system_prompt = (system_prompt + "\n\nPlease always respond in English.").strip()
    if not system_prompt:
        return messages
    return [{"role": "system", "content": system_prompt}] + list(messages)


@chat_bp.route("/api/chat", methods=["POST"])
def api_chat():
    """对话 API：provider_id + model；完整消息历史；可选 conversation_id、use_utcp_tools、use_deep_thinking、attachment_paths"""
    data = request.get_json() or {}
    provider_id = data.get("provider_id")
    model = data.get("model")
    messages = data.get("messages", [])
    conversation_id = data.get("conversation_id")
    use_utcp_tools = data.get("use_utcp_tools") is True
    use_deep_thinking = data.get("use_deep_thinking") is True
    attachment_paths = data.get("attachment_paths") or []
    if not provider_id or not model or not messages:
        return jsonify({"error": "缺少 provider_id、model 或 messages"}), 400
    cfg = current_app.config["CONFIG_LOADER"]()
    lock_model = bool(cfg.get("conversation_lock_model", True))
    if lock_model and conversation_id:
        conv = get_conversation(conversation_id)
        if conv and conv.get("provider_id") is not None and conv.get("model") is not None:
            if conv.get("provider_id") != provider_id or conv.get("model") != model:
                return jsonify({"error": "该对话已由固定模型维护，请使用对话绑定的模型继续"}), 400
    messages = _inject_system_prompt(messages, use_utcp_tools, data)
    messages = _inject_attachment_paths(messages, attachment_paths)
    cfg = current_app.config["CONFIG_LOADER"]()
    messages = _apply_weknora_memory_if_enabled(messages, cfg)
    max_tool_rounds = int(cfg.get("utcp_max_tool_rounds", 50))
    if cfg.get("utcp_unlimited_rounds"):
        max_tool_rounds = 9999
    last_user = messages[-1].get("content", "") if messages else ""
    use_tools_this_turn = use_utcp_tools and not _is_progress_or_status_query(last_user)
    try:
        if use_tools_this_turn:
            content = chat_completion_with_tools(
                provider_id=provider_id, model=model, messages=messages,
                max_tool_rounds=max_tool_rounds, use_deep_thinking=use_deep_thinking,
            )
        else:
            result = chat_completion(provider_id=provider_id, model=model, messages=messages)
            content = (result.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        model_label = _model_label(provider_id, model)
        if conversation_id:
            conv = get_conversation(conversation_id)
            if conv:
                if lock_model and (conv.get("provider_id") is None or conv.get("model") is None):
                    update_conversation(conversation_id, provider_id=provider_id, model=model)
                new_messages = conv.get("messages", []) + [
                    {"role": "user", "content": last_user},
                    {"role": "assistant", "content": content, "model_label": model_label},
                ]
                update_conversation(conversation_id, messages=new_messages)
                if len(new_messages) == 2:
                    summary = summarize_conversation_title(provider_id, model, last_user, content)
                    if summary:
                        update_conversation(conversation_id, title=summary)
                try:
                    from services.weknora_memory import append_turn_to_memory
                    turn_text = "User: " + (last_user or "") + "\n\nAssistant: " + (content[:12000] if len(content) > 12000 else content)
                    append_turn_to_memory(conversation_id, turn_text)
                except Exception:
                    pass
        else:
            conv = create_conversation(
                title=(last_user[:50] if last_user else "新对话"),
                messages=[
                    messages[-2] if len(messages) >= 2 else messages[-1],
                    {"role": "assistant", "content": content, "model_label": model_label},
                ],
                provider_id=provider_id if lock_model else None,
                model=model if lock_model else None,
            )
            conversation_id = conv["id"]
            summary = summarize_conversation_title(provider_id, model, last_user, content)
            if summary:
                update_conversation(conversation_id, title=summary)
        return jsonify({"choices": [{"message": {"content": content}}], "conversation_id": conversation_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/api/chat/stream", methods=["POST"])
def api_chat_stream():
    """流式对话：provider_id + model；SSE；可选 use_utcp_tools、use_deep_thinking、attachment_paths；完整消息历史与对话持久化"""
    data = request.get_json() or {}
    provider_id = data.get("provider_id")
    model = data.get("model")
    messages = data.get("messages", [])
    conversation_id = data.get("conversation_id")
    use_utcp_tools = data.get("use_utcp_tools") is True
    use_deep_thinking = data.get("use_deep_thinking") is True
    attachment_paths = data.get("attachment_paths") or []
    if not provider_id or not model or not messages:
        _chat_debug("流式对话请求无效: 缺少 provider_id/model/messages")
        return jsonify({"error": "缺少 provider_id、model 或 messages"}), 400
    cfg = current_app.config["CONFIG_LOADER"]()
    lock_model = bool(cfg.get("conversation_lock_model", True))
    if lock_model and conversation_id:
        conv = get_conversation(conversation_id)
        if conv and conv.get("provider_id") is not None and conv.get("model") is not None:
            if conv.get("provider_id") != provider_id or conv.get("model") != model:
                _chat_debug("流式对话被拒绝: 对话已绑定 provider_id=%s model=%s" % (conv.get("provider_id"), conv.get("model")))
                return jsonify({"error": "该对话已由固定模型维护，请使用对话绑定的模型继续"}), 400
    _chat_debug("流式对话开始: provider_id=%s model=%s use_utcp_tools=%s use_deep_thinking=%s messages_count=%s" % (
        provider_id, model, use_utcp_tools, use_deep_thinking, len(messages)))
    messages = _inject_system_prompt(messages, use_utcp_tools, data)
    messages = _inject_attachment_paths(messages, attachment_paths)
    cfg = current_app.config["CONFIG_LOADER"]()
    messages = _apply_weknora_memory_if_enabled(messages, cfg)
    max_tool_rounds = int(cfg.get("utcp_max_tool_rounds", 50))
    if cfg.get("utcp_unlimited_rounds"):
        max_tool_rounds = 9999

    last_user = messages[-1].get("content", "") if messages else ""
    use_tools_this_turn = use_utcp_tools and not _is_progress_or_status_query(last_user)
    model_label = _model_label(provider_id, model)

    def _save_partial(cid, content_parts, steps, plan_content=None):
        """流式过程中将当前进度写入对话（含计划阶段），便于刷新/切换后恢复"""
        if not cid:
            return
        conv = get_conversation(cid)
        if not conv:
            return
        raw = "".join(content_parts)
        partial_content = ("【当前情况与计划】\n\n" + plan_content + "\n\n---\n\n" + raw) if plan_content else raw
        assistant_msg = {"role": "assistant", "content": partial_content, "model_label": model_label}
        if steps:
            assistant_msg["tool_steps"] = list(steps)
        msgs = conv.get("messages", [])
        if msgs and msgs[-1].get("role") == "assistant":
            new_messages = msgs[:-1] + [assistant_msg]
        else:
            new_messages = msgs + [assistant_msg]
        update_conversation(cid, messages=new_messages)

    def generate():
        full_content = []
        tool_steps = []  # 收集本轮工具调用，用于持久化
        plan_content = ""  # 第一轮后的「当前情况与计划」，用于与最终内容合并
        save_interval = 0
        cid = conversation_id
        _chat_debug("AI对话 用户消息: %s" % ((last_user or "")[:200] or "(空)"))
        if not cid:
            conv = create_conversation(
                title=(last_user[:50] if last_user else "新对话"),
                messages=[{"role": "user", "content": last_user}],
                provider_id=provider_id if lock_model else None,
                model=model if lock_model else None,
            )
            cid = conv["id"]
            yield f"data: {json.dumps({'conversation_id': cid}, ensure_ascii=False)}\n\n"
        else:
            conv = get_conversation(cid)
            if conv:
                if lock_model and (conv.get("provider_id") is None or conv.get("model") is None):
                    update_conversation(cid, provider_id=provider_id, model=model)
                new_msgs = conv.get("messages", []) + [{"role": "user", "content": last_user}]
                update_conversation(cid, messages=new_msgs)
        try:
            for chunk in chat_completion_stream(
                provider_id=provider_id, model=model, messages=messages,
                use_utcp_tools=use_tools_this_turn, use_deep_thinking=use_deep_thinking,
                max_tool_rounds=max_tool_rounds,
            ):
                if use_tools_this_turn and isinstance(chunk, dict):
                    ev = chunk
                    if ev.get("type") == "content":
                        full_content.append(ev.get("content") or "")
                        save_interval += 1
                    elif ev.get("type") == "plan":
                        plan_content = ev.get("content") or ""
                        save_interval += 1
                        _chat_debug("自动化任务栏 计划: %s" % ((plan_content or "")[:300]))
                    elif ev.get("type") == "tool_call":
                        tool_steps.append({
                            "name": ev.get("name") or "",
                            "arguments_preview": ev.get("arguments_preview") or "",
                            "result_summary": "",
                            "result_full": "",
                            "step_index": ev.get("step_index"),
                            "step_total": ev.get("step_total"),
                        })
                        save_interval += 1
                        _chat_debug("自动化任务栏 工具调用: %s %s" % (ev.get("name") or "", (ev.get("arguments_preview") or "")[:200]))
                    elif ev.get("type") == "tool_result" and tool_steps:
                        summary = (ev.get("result_summary") or ev.get("result_full") or "")[:2000]
                        full_result = (ev.get("result_full") or ev.get("result_summary") or "")[:8000]
                        for st in tool_steps:
                            if not st.get("result_summary") and not st.get("result_full"):
                                st["result_summary"] = summary
                                st["result_full"] = full_result
                                st["success"] = ev.get("success") is not False
                                if ev.get("elapsed_seconds") is not None:
                                    st["elapsed_seconds"] = ev.get("elapsed_seconds")
                                break
                        save_interval += 1
                        _chat_debug("自动化任务栏 工具结果: %s" % ((summary or full_result or "")[:300]))
                    if save_interval >= 1:
                        save_interval = 0
                        _save_partial(cid, full_content, tool_steps, plan_content)
                        raw = "".join(full_content)
                        if raw:
                            _chat_debug("AI对话 助手内容(片段): %d 字" % len(raw))
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                else:
                    full_content.append(chunk if isinstance(chunk, str) else "")
                    save_interval += 1
                    if save_interval >= 1:
                        save_interval = 0
                        _save_partial(cid, full_content, tool_steps)
                        _chat_debug("AI对话 助手内容(片段): %d 字" % len("".join(full_content)))
                    yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
            content = ("【当前情况与计划】\n\n" + plan_content + "\n\n---\n\n" + "".join(full_content)) if plan_content else "".join(full_content)
            assistant_msg = {"role": "assistant", "content": content, "model_label": model_label}
            if tool_steps:
                assistant_msg["tool_steps"] = tool_steps
            conv = get_conversation(cid)
            if conv:
                msgs = conv.get("messages", [])
                if msgs and msgs[-1].get("role") == "assistant":
                    new_messages = msgs[:-1] + [assistant_msg]
                else:
                    new_messages = msgs + [assistant_msg]
                update_conversation(cid, messages=new_messages)
                if len(new_messages) == 2:
                    summary = summarize_conversation_title(provider_id, model, last_user, content)
                    if summary:
                        update_conversation(cid, title=summary)
                try:
                    from services.weknora_memory import append_turn_to_memory
                    turn_text = "User: " + (last_user or "") + "\n\nAssistant: " + (content[:12000] if len(content) > 12000 else content)
                    if tool_steps:
                        turn_text += "\n\nTools: " + ", ".join((s.get("name") or "") for s in tool_steps[:20])
                    append_turn_to_memory(cid, turn_text)
                except Exception:
                    pass
            yield f"data: {json.dumps({'conversation_id': cid, 'model_label': model_label}, ensure_ascii=False)}\n\n"
            _chat_debug("流式对话完成: conversation_id=%s 助手回复 %d 字" % (cid, len(content)))
        except Exception as e:
            _chat_debug("流式对话异常: %s" % str(e))
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
