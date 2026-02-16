# -*- coding: utf-8 -*-
"""在应用内执行 UTCP 工具，供对话中模型触发的 tool_call 使用。"""
import json

from . import datetime_tool
from . import shell_tool
from . import file_tool


def execute_tool(name: str, arguments: dict) -> str:
    """
    根据工具名称与参数执行对应 UTCP 逻辑，返回 JSON 字符串（作为 tool 消息的 content）。
    若工具不存在或执行异常，返回包含 error 的 JSON 字符串。
    """
    args = arguments if isinstance(arguments, dict) else {}
    try:
        if name == "get_current_time":
            tz = args.get("timezone_hours")
            if tz is not None:
                try:
                    tz = float(tz)
                except (TypeError, ValueError):
                    tz = None
            result = datetime_tool.get_datetime(timezone_hours=tz)
            return json.dumps(result, ensure_ascii=False)

        if name == "run_shell":
            cmd = args.get("command") or ""
            timeout = args.get("timeout_seconds")
            cwd = args.get("cwd")
            result = shell_tool.run_shell(command=cmd, timeout_seconds=timeout, cwd=cwd)
            return json.dumps(result, ensure_ascii=False)

        if name == "read_file":
            path = args.get("path") or ""
            encoding = args.get("encoding")
            max_bytes = args.get("max_bytes")
            result = file_tool.read_file(path=path, encoding=encoding, max_bytes=max_bytes)
            return json.dumps(result, ensure_ascii=False)

        if name == "write_file":
            path = args.get("path") or ""
            content = args.get("content")
            if content is None:
                content = ""
            encoding = args.get("encoding")
            append = args.get("append") is True
            result = file_tool.write_file(path=path, content=content, encoding=encoding, append=append)
            return json.dumps(result, ensure_ascii=False)

        if name == "list_dir":
            path = args.get("path") or "."
            include_hidden = args.get("include_hidden") is True
            result = file_tool.list_dir(path=path, include_hidden=include_hidden)
            return json.dumps(result, ensure_ascii=False)

        return json.dumps({"success": False, "error": f"未知工具: {name}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
