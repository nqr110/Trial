# -*- coding: utf-8 -*-
"""
LLM 服务：从 config.providers 读取服务商列表，按 provider_id + model 调用。
支持将 UTCP 工具以 OpenAI function 形式传给模型，并处理 model 返回的 tool_calls。
"""
import json
import requests
from flask import current_app


def _get_config():
    return current_app.config["CONFIG_LOADER"]()


def get_available_models():
    """返回固定服务商+模型列表，每项为「服务商 - 模型」选项（含是否支持 Function Calling、深度思考）"""
    fixed = current_app.config.get("FIXED_PROVIDER_MODELS") or []
    return [
        {
            "provider_id": m["provider_id"],
            "model": m["model"],
            "label": f"{m['provider_name']} - {m['model']}",
            "support_function_calling": m.get("support_function_calling", False),
            "support_deep_thinking": m.get("support_deep_thinking", False),
        }
        for m in fixed
    ]


def _openai_style_chat_sync(api_base: str, api_key: str, model: str, messages: list, tools: list = None, extra_body: dict = None) -> dict:
    """非流式请求，返回完整 JSON。与流式拆开，避免含 yield 时整函数变成生成器。"""
    url = (api_base.rstrip("/") + "/v1/chat/completions") if api_base else ""
    no_url_msg = {"choices": [{"message": {"content": "请先在后台配置该服务商的 API 地址与 Key。"}}]}
    if not url:
        return no_url_msg
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "stream": False}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if extra_body:
        payload.update(extra_body)
    r = requests.post(url, json=payload, headers=headers, timeout=120)
    r.raise_for_status()
    return r.json()


def _openai_style_chat_stream(api_base: str, api_key: str, model: str, messages: list, extra_body: dict = None):
    """流式请求，yield content 块。"""
    url = (api_base.rstrip("/") + "/v1/chat/completions") if api_base else ""
    if not url:
        yield "请先在后台配置该服务商的 API 地址与 Key。"
        return
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "stream": True}
    if extra_body:
        payload.update(extra_body)
    r = requests.post(url, json=payload, headers=headers, timeout=120, stream=True)
    r.raise_for_status()
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.strip():
            continue
        if line.startswith("data: "):
            data = line[6:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
                delta = (obj.get("choices") or [{}])[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content
            except json.JSONDecodeError:
                pass


def _openai_style_chat(api_base: str, api_key: str, model: str, messages: list, stream: bool = False, tools: list = None, extra_body: dict = None):
    """OpenAI 兼容接口；stream=True 时 yield content 块；stream=False 时返回 dict。"""
    if stream:
        return _openai_style_chat_stream(api_base, api_key, model, messages, extra_body=extra_body)
    return _openai_style_chat_sync(api_base, api_key, model, messages, tools=tools, extra_body=extra_body)


def _get_provider_config(provider_id: str):
    """从 config.providers 中按 id 查找，返回 (api_base, api_key)"""
    cfg = _get_config()
    providers = cfg.get("providers") or []
    for p in providers:
        if isinstance(p, dict) and p.get("id") == provider_id:
            return (p.get("api_base") or "", p.get("api_key") or "")
    return ("", "")


def chat_completion(provider_id: str, model: str, messages: list) -> dict:
    """按服务商 id + 模型名调用"""
    api_base, api_key = _get_provider_config(provider_id)
    return _openai_style_chat(api_base, api_key, model, messages, stream=False)


def chat_completion_with_tools(provider_id: str, model: str, messages: list, max_tool_rounds: int = 20, use_deep_thinking: bool = False) -> str:
    """
    带 UTCP 工具调用的对话（自动化工作流）：向模型传入工具定义，若模型返回 tool_calls 则执行并继续请求，
    直到模型返回纯文本或达到最大轮数。返回最终助手回复内容。
    max_tool_rounds 默认 20，支持多步自动化。若服务商 API 不支持 tools 则回退为普通对话。
    use_deep_thinking：是否启用深度思考（如 DeepSeek 的 reasoning 模式）。
    """
    from utcp.tools_def import get_openai_tools
    from utcp.tool_executor import execute_tool

    api_base, api_key = _get_provider_config(provider_id)
    tools = get_openai_tools()
    current_messages = list(messages)
    use_tools = True  # 若某次带 tools 的请求失败，则不再传 tools
    extra_body = {}
    if use_deep_thinking:
        # DeepSeek 官方文档：thinking 参数为 {"type": "enabled"}
        extra_body = {"thinking": {"type": "enabled"}}

    for _ in range(max_tool_rounds):
        try:
            resp = _openai_style_chat(
                api_base, api_key, model, current_messages, stream=False,
                tools=tools if use_tools else None,
                extra_body=extra_body if extra_body else None,
            )
        except Exception:
            if use_tools:
                use_tools = False
                resp = _openai_style_chat(
                    api_base, api_key, model, current_messages, stream=False,
                    tools=None, extra_body=extra_body if extra_body else None,
                )
            else:
                raise
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls")
        content = (msg.get("content") or "").strip()

        if not tool_calls:
            return content or ""

        # 追加助手消息（含 tool_calls）
        current_messages.append(msg)
        # 执行每个 tool_call 并追加 tool 消息
        for tc in tool_calls:
            tid = tc.get("id") or ""
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = execute_tool(name, args)
            current_messages.append({"role": "tool", "tool_call_id": tid, "content": result})

    return content or ""


def _tool_result_summary(result_json: str, max_len: int = 280) -> str:
    """从工具返回的 JSON 中提取简短摘要，用于步骤栏展示。"""
    try:
        obj = json.loads(result_json) if isinstance(result_json, str) else result_json
        if not obj.get("success"):
            return obj.get("message") or obj.get("error") or "执行失败"
        data = obj.get("data")
        if data is None:
            return obj.get("message") or "ok"
        if isinstance(data, dict):
            if "stdout" in data and data["stdout"]:
                s = (data["stdout"] or "").strip()
                return s[:max_len] + ("…" if len(s) > max_len else "")
            if "content" in data:
                s = (data.get("content") or "").strip()
                return s[:max_len] + ("…" if len(s) > max_len else "")
            if "summary" in data:
                return (data.get("summary") or "")[:max_len]
            if "entries" in data:
                n = len(data.get("entries") or [])
                return f"共 {n} 项"
        return str(data)[:max_len]
    except Exception:
        return (result_json or "")[:max_len] + ("…" if len(str(result_json or "")) > max_len else "")


def chat_completion_stream_with_tool_events(provider_id: str, model: str, messages: list, max_tool_rounds: int = 20, use_deep_thinking: bool = False):
    """
    带 UTCP 工具的工作流流式调用：每轮中先 yield tool_call 事件，执行工具后 yield tool_result 事件，
    最后若无 tool_calls 则 yield content 事件（最终回复的逐块内容）。
    前端可据此展示「操作步骤」子信息栏。
    """
    from utcp.tools_def import get_openai_tools
    from utcp.tool_executor import execute_tool

    api_base, api_key = _get_provider_config(provider_id)
    tools = get_openai_tools()
    current_messages = list(messages)
    use_tools = True
    extra_body = {"thinking": {"type": "enabled"}} if use_deep_thinking else {}

    for _ in range(max_tool_rounds):
        try:
            resp = _openai_style_chat(
                api_base, api_key, model, current_messages, stream=False,
                tools=tools if use_tools else None,
                extra_body=extra_body if extra_body else None,
            )
        except Exception:
            if use_tools:
                use_tools = False
                resp = _openai_style_chat(
                    api_base, api_key, model, current_messages, stream=False,
                    tools=None, extra_body=extra_body if extra_body else None,
                )
            else:
                raise
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls")
        content = (msg.get("content") or "").strip()

        if not tool_calls:
            chunk_size = 8
            for i in range(0, len(content or ""), chunk_size):
                yield {"type": "content", "content": (content or "")[i : i + chunk_size]}
            return

        current_messages.append(msg)
        for tc in tool_calls:
            tid = tc.get("id") or ""
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            args_preview = json.dumps(args, ensure_ascii=False)[:120]
            yield {"type": "tool_call", "tool_call_id": tid, "name": name, "arguments": args, "arguments_preview": args_preview}
            result = execute_tool(name, args)
            summary = _tool_result_summary(result)
            yield {"type": "tool_result", "tool_call_id": tid, "name": name, "result_summary": summary, "result_full": result[:2000] if len(result) > 2000 else result}
            current_messages.append({"role": "tool", "tool_call_id": tid, "content": result})

    yield {"type": "content", "content": ""}


def chat_completion_stream(provider_id: str, model: str, messages: list, use_utcp_tools: bool = False, use_deep_thinking: bool = False):
    """
    流式调用。若 use_utcp_tools 为 True，yield 的为事件对象 {type, ...}（tool_call / tool_result / content），
    供前端展示步骤栏与最终内容；否则 yield 纯 content 字符串块。
    """
    if use_utcp_tools:
        for ev in chat_completion_stream_with_tool_events(provider_id, model, messages, use_deep_thinking=use_deep_thinking):
            yield ev
        return
    api_base, api_key = _get_provider_config(provider_id)
    extra_body = {"thinking": {"type": "enabled"}} if use_deep_thinking else None
    for chunk in _openai_style_chat(api_base, api_key, model, messages, stream=True, extra_body=extra_body):
        yield chunk
