# Trial Web 应用

基于 Python (Flask) 的 Web 应用，支持多模型对话、后台 API 配置、UTCP 协议接口与知识库检索。知识库支持**本地 knowledge 目录**或可选接入**腾讯开源 WeKnora** 语义检索。

## 功能

- **全局登录**：默认账号 `root` / 密码 `itzx`
- **对话页**：选择阿里云百炼或 DeepSeek 等模型，支持流式回复；可选启用自动化工作流（UTCP 工具：执行命令、读写文件、知识库检索等）
- **设置**：全局配置（API 检测、UTCP 开关、安全模式、AI 默认语言、前置提示词）、API 信息、知识库与 WeKnora 配置、UTCP 控制台、关于我们
- **知识库**：AI 通过 `search_knowledge` 工具按需检索。推荐配置 [WeKnora](https://github.com/Tencent/WeKnora) 使用语义检索；未配置时使用项目 `knowledge/` 目录下 `.md`、`.txt` 的关键词检索，适用于 CTF、漏洞利用等场景
- **安全模式**：开启后禁止 AI 修改项目自身文件（仅允许写入上传目录）
- **UTCP 接口**：`utcp/` 包提供健康检查、日期时间、Shell、文件读写等；Web 端有「UTCP 控制台」可调用并查看响应

## 环境

- Python 3.11（建议使用 conda 在项目下创建环境）

激活环境：

```bash
conda activate Trial
```

若需创建名为 `Trial` 的 conda 环境：

```bash
conda create -n Trial python=3.11 -y
conda activate Trial
```

## 安装与运行

```bash
# Conda环境
conda activate Trial
pip install -r requirements.txt
#mimproxy环境
apt install mitmproxy
# 运行
python app.py
```

访问：<http://127.0.0.1:5000>，使用 `root` / `itzx` 登录。
代理：服务器:8888

### 可选：接入 WeKnora 语义检索

[WeKnora](https://github.com/Tencent/WeKnora) 是腾讯开源的文档理解与语义检索框架（RAG），支持 PDF/Word/图片等多格式、向量+关键词+知识图谱混合检索。配置后，`search_knowledge` 将调用 WeKnora 的 `/api/v1/knowledge-search` 接口，获得更精准的语义召回。

1. 部署 WeKnora（与 Trial 分离部署，例如同机 Docker）：
   ```bash
   git clone https://github.com/Tencent/WeKnora.git
   cd WeKnora && cp .env.example .env
   # 编辑 .env 后启动
   ./scripts/start_all.sh
   ```
   WeKnora 默认后端 API：`http://localhost:8080`，Web UI：`http://localhost`。

2. 在 Trial 中配置：进入 **设置 → 知识库**，填写 **Base URL**（如 `http://localhost:8080`）、**API Key**（WeKnora 后台获取）、**知识库 ID**（可选，不填则搜索全部）。保存后，对话中的 `search_knowledge` 将优先走 WeKnora。

3. **可选：WeKnora 对话记忆**：若需支持长对话与自动化任务中的大量信息，可在同一设置页勾选「启用对话记忆」、填写**记忆知识库 ID**（需在 WeKnora 中单独建一个知识库用于存对话）、设置「最近保留轮数」（如 20）。启用后，每轮结束会将该轮摘要写入 WeKnora，下次请求时只送「最近 N 轮 + 按当前问题检索到的相关记忆」，避免整段 history 挤爆上下文。

## 项目架构

当前整体架构如下（知识库可选用本地目录或 WeKnora 两种后端）：

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                    用户浏览器                             │
                    └─────────────────────────┬───────────────────────────────┘
                                              │ HTTPS (可选) / HTTP
                                              ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              Trial (Flask)                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────────┐  │
│  │  auth        │  │  chat        │  │  settings    │  │  routes/browser, admin       │  │
│  │  登录/会话   │  │  对话/流式   │  │  全局/API/   │  │  代理/录包/管理               │  │
│  │              │  │  /api/chat/* │  │  知识库/UTCP │  │                              │  │
│  └──────────────┘  └──────┬───────┘  └──────────────┘  └──────────────────────────────┘  │
│                            │                                                              │
│  ┌─────────────────────────▼──────────────────────────────────────────────────────────┐  │
│  │  services: llm | knowledge_base (检索) | weknora_memory (记忆检索/写入) | conversation_store | browser_packets │  │
│  └─────────────────────────┬──────────────────────────────────────────────────────────┘  │
│                            │                                                              │
│  ┌─────────────────────────▼──────────────────────────────────────────────────────────┐  │
│  │  utcp: tools_def (OpenAI tools) | tool_executor (run_shell, read_file, search_*)     │  │
│  └─────────────────────────┬──────────────────────────────────────────────────────────┘  │
└────────────────────────────┼────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ 外部 LLM API     │  │ 知识库后端       │  │ 本地资源         │
│ (百炼/DeepSeek/  │  │ ① WeKnora       │  │ knowledge/      │
│  SiliconFlow 等)│  │    (可选)        │  │ config.json     │
│                 │  │   POST /api/v1/  │  │ data/ uploads/  │
│                 │  │   knowledge-     │  │                 │
│                 │  │   search         │  │                 │
│                 │  │ ② 本地 keyword   │  │                 │
│                 │  │    search        │  │                 │
│                 │  │   (knowledge/*)  │  │                 │
│                 │  └─────────────────┘  └─────────────────┘
└─────────────────┘
```

- **前端**：对话页、设置页、UTCP 控制台等，通过 Flask 模板与静态资源提供。
- **后端**：Flask 蓝图（auth、chat、settings、admin、browser、utcp），业务逻辑在 `services/`，工具执行在 `utcp/`。
- **知识库**：`search_knowledge` 若配置了 WeKnora Base URL，则请求 WeKnora 语义检索；否则使用 `knowledge/` 下 .md/.txt 的本地关键词检索。
- **对话记忆**：启用 WeKnora 对话记忆后，上下文仅由 WeKnora 检索结果与当前问题组成，不再使用本地最近 N 轮消息；每轮结束后将本轮摘要写入 WeKnora 记忆 KB。
- **配置与数据**：`config.json`（API、WeKnora、对话记忆开关等）、`data/conversations.json`（对话历史）、`knowledge/`（本地知识文件）。

## 接口说明

| 说明           | 路径/方法        |
|----------------|------------------|
| 登录           | GET/POST `/auth/login` |
| 对话页         | GET `/`          |
| 流式对话       | POST `/api/chat/stream`，body: `provider_id`, `model`, `messages`, `use_utcp_tools` 等 |
| 模型与配置     | GET `/api/models` |
| 设置-全局配置  | GET `/settings/global` |
| 设置-知识库    | GET `/settings/knowledge` |
| 知识库-WeKnora 配置 | GET/POST `/settings/knowledge/api/weknora` |
| UTCP 健康检查  | GET `/api/utcp/health` |
| UTCP 控制台页  | GET `/utcp`（重定向至设置下 UTCP 控制台） |

## 配置存储

- **config.json**：API 与全局配置（含 `weknora_base_url`、`weknora_api_key`、`weknora_knowledge_base_id`、`weknora_memory_enabled`、`weknora_memory_kb_id`、`weknora_memory_max_recent_turns` 等）；对话历史在 `data/conversations.json`。
- **知识库**：配置 WeKnora 后以 WeKnora 知识库为准；未配置时使用项目下 `knowledge/` 目录。启用「WeKnora 对话记忆」时，会向指定记忆知识库写入每轮摘要，请求时仅用检索到的相关记忆与当前问题作为上下文，以支持长对话。
