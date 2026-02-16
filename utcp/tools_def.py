# -*- coding: utf-8 -*-
"""UTCP 工具在 OpenAI 兼容接口中的定义（供对话中模型调用）。"""


def get_openai_tools():
    """
    返回可在 /v1/chat/completions 中传入的 tools 列表（OpenAI 格式）。
    模型可根据用户问题决定是否调用这些工具。支持自动化工作流：Shell、文件、目录等。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "get_current_time",
                "description": "获取当前日期和时间（默认中国北京时间 UTC+8）。当用户询问现在几点、今天几号、当前时间、星期几、时间戳时，应调用此工具。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "timezone_hours": {
                            "type": "number",
                            "description": "可选。时区偏移小时数，如 8 表示北京时间，-5 表示美东。不传则使用北京时间(UTC+8)。",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_shell",
                "description": "在服务器（Linux）上执行任意 shell 命令。可用于执行系统命令、脚本、查看进程、安装软件、编译、运行程序等。你拥有与人类操作者相当的权限，可完成用户要求的各类操作。执行前应想清楚命令是否安全、必要时分步执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "要执行的完整 shell 命令，例如：ls -la /tmp、nmap -sT 192.168.1.1、cat /etc/os-release。",
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "description": "可选。超时秒数，默认 60，最大 600。",
                        },
                        "cwd": {
                            "type": "string",
                            "description": "可选。执行命令时的工作目录路径。",
                        },
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取服务器上的文件内容（Linux 路径）。路径可为相对项目根或绝对路径（需在项目根下）。用于查看配置、日志、代码等。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件路径，例如 config.json、/opt/VCSG/Trial/README.md。",
                        },
                        "encoding": {
                            "type": "string",
                            "description": "可选。文件编码，默认 utf-8。",
                        },
                        "max_bytes": {
                            "type": "integer",
                            "description": "可选。最大读取字节数，默认 512KB。",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "向服务器上的文件写入内容（Linux 路径）。若目录不存在会尝试创建。用于创建或修改配置、脚本、代码等。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件路径。",
                        },
                        "content": {
                            "type": "string",
                            "description": "要写入的完整内容。",
                        },
                        "encoding": {
                            "type": "string",
                            "description": "可选。编码，默认 utf-8。",
                        },
                        "append": {
                            "type": "boolean",
                            "description": "可选。是否追加到文件末尾，默认 false 为覆盖。",
                        },
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "列出目录下的文件和子目录（Linux 路径）。用于浏览项目结构、查找文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "目录路径，默认为项目根目录。",
                        },
                        "include_hidden": {
                            "type": "boolean",
                            "description": "可选。是否包含以 . 开头的隐藏文件/目录，默认 false。",
                        },
                    },
                },
            },
        },
    ]
