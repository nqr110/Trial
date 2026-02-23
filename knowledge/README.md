# CTF 与网络安全知识库

当未配置 WeKnora 时，本目录下的 `.md` 与 `.txt` 文件会被对话中的 AI 通过 **search_knowledge** 工具按关键词检索；若已在设置中配置 WeKnora，则检索走 WeKnora 语义检索，本目录仅作备用或说明。内容用于 CTF 解题、漏洞利用、工具用法等参考。

## 知识库结构

- **Web 安全**：SQL 注入、XSS、SSTI、文件包含、反序列化等
- **Web 安全渗透测试流程**：七步标准流程及与工具（preview_web_page、sqlmap 等）的对应关系
- **sqlmap**：完整参数与使用说明（基本用法、探测枚举、level/risk、tamper、示例命令等）
- **二进制安全**：Pwn、逆向工程、缓冲区溢出、ROP 等
- **密码学**：古典密码、现代密码、常见攻击方式
- **取证与隐写**：图片、音频、文件隐写分析
- **杂项（Misc）**：编码、协议分析、自动化脚本
- **工具速查**：常用命令与参数

## 使用说明

在 CTF 解题过程中，可通过 `search_knowledge` 工具查询相关知识点，例如：
- "SQL 注入 union select"
- "Python 反序列化 payload"
- "base64 解码 linux 命令"

每个文件按主题组织，包含原理、典型场景、常用 payload 和解题思路。