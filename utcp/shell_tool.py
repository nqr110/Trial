# -*- coding: utf-8 -*-
"""UTCP 工具：在 Linux 上执行 shell 命令。仅考虑 Linux 兼容性。"""
import subprocess

from flask import request, jsonify
from .blueprint import utcp_bp


def run_shell(command: str, timeout_seconds: int = 60, cwd: str = None, **kwargs) -> dict:
    """
    在服务器上执行 shell 命令（Linux）。
    参数：
        command: 要执行的命令字符串。
        timeout_seconds: 超时秒数，默认 60。
        cwd: 可选，工作目录路径。
    返回：
        含 success、stdout、stderr、returncode、error 的 dict。
    """
    if not command or not str(command).strip():
        return {
            "success": False,
            "protocol": "UTCP",
            "message": "command 不能为空",
            "data": None,
        }
    try:
        timeout = max(1, min(600, int(timeout_seconds))) if timeout_seconds is not None else 60
    except (TypeError, ValueError):
        timeout = 60
    try:
        proc = subprocess.run(
            ["/bin/sh", "-c", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or None,
        )
        return {
            "success": True,
            "protocol": "UTCP",
            "message": "ok",
            "data": {
                "stdout": proc.stdout or "",
                "stderr": proc.stderr or "",
                "returncode": proc.returncode,
            },
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "protocol": "UTCP",
            "message": f"命令执行超时（{timeout} 秒）",
            "data": None,
        }
    except Exception as e:
        return {
            "success": False,
            "protocol": "UTCP",
            "message": str(e),
            "data": None,
        }


@utcp_bp.route("/shell", methods=["POST"])
def utcp_shell():
    """UTCP 工具：执行 shell 命令。POST body: command, timeout_seconds?, cwd?"""
    data = request.get_json() or {}
    data = data if isinstance(data, dict) else {}
    result = run_shell(
        command=data.get("command") or "",
        timeout_seconds=data.get("timeout_seconds"),
        cwd=data.get("cwd"),
    )
    return jsonify(result)
