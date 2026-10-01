# 🔬 研究助手智能体系统

一个能自动检索学术资料、分析论文、生成本地知识库、并支持多轮问答的研究助手。

采用**三层递进架构**：**ReAct 核心闭环 → RAG 增强 → 多智能体协作**，支持 OpenAI / 智谱 GLM / 通义千问 / DeepSeek / Ollama / LM Studio 六种 LLM 提供商，云端与本地（免费）均可使用。

```
                 ┌─────────────────────────────────────────────────┐
                 │          Web UI（FastAPI + 原生 SPA，v2.0）      │
                 │       💬对话   📚知识库   🔬研究   ⚙️设置        │
                 │       页面切换零刷新，无框架纯 JS 前端           │
                 └───────────────────────┬─────────────────────────┘
                                         │
        ┌────────────────────────────────┼────────────────────────────────┐
        ▼                                ▼                                ▼
┌───────────────┐              ┌─────────────────┐             ┌──────────────────┐
│ 第一层         │              │ 第二层           │             │ 第三层            │
│ ReAct 核心闭环 │              │ RAG 增强         │             │ 多智能体协作       │
│               │              │                 │             │                  │
│ Reason→Act    │──调用──▶     │ PDF/URL 解析     │             │ Supervisor 路由   │
│ →Execute→Observe             │ ChromaDB 向量库  │             │ 研究员→分析员      │
│ 短期/长期记忆  │              │ 检索/重排/生成    │             │ →总结员→质检       │
│ 防死循环/预算  │              │                 │             │ (LangGraph)       │
└───────────────┘              └─────────────────┘             └──────────────────┘
```

## 功能特性

- **多 LLM 支持**：OpenAI / 智谱 GLM / 通义千问 / DeepSeek / **自定义 API（OpenAI 兼容，可连任意中转/自建网关）** / Ollama / LM Studio，云端 Key 与本地模型自由切换；模型名支持**自定义输入**（预设列表之外可手填任意模型名），Ollama/LM Studio 自动发现本地已安装/已加载模型
- **第一层 · ReAct 核心闭环**：工具调用 + 短期（滑动窗口 + LLM 摘要压缩）/长期记忆 + 防死循环 + Token 预算控制
- **第二层 · RAG 增强**：PDF/TXT/Markdown/HTML/URL → 文档切分 → ChromaDB 向量检索 → 知识库问答（支持多集合管理）
- **第三层 · 多智能体协作**：Supervisor 路由 → 研究员收集 → 分析员综合 → 总结员成稿 → 质量检查回退，LangGraph 编排
- **工具系统**：国内搜索（百度优先、必应兜底，无需 Key）、**学术文献检索（Crossref 国际期刊 + 万方中文论文）**、DuckDuckGo 搜索（可选）、网页抓取、PDF 解析、知识库检索（注册表模式，可扩展）；LLM 限流（429）自动识别并给出友好提示
- **Web UI（v2.0 原生 SPA）**：FastAPI + 纯 HTML/JS 单页应用，页面切换零重载（彻底解决切换卡顿）；玻璃拟态风格，侧边栏导航（对话 / 知识库 / 研究 / 设置四页），Agent 思考过程实时追踪（工具调用/观察记录），历史报告网页端直接下载/删除
- **记忆与安全**：Token 预算强制终止、重复 Action 循环检测、幻觉粗筛、健康检查三级状态

## 快速开始

### 环境要求

- Python 3.9+（推荐 3.11）
- 至少一个 LLM 提供商可用：云端 API Key，或本地 Ollama / LM Studio

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入至少一个 LLM 提供商的 API Key
```

> 云端提供商任选其一即可（OpenAI / 智谱 / 通义 / DeepSeek）。
> 想完全免费使用？直接使用本地 Ollama，见下方「使用本地 Ollama（免费）」。

### 3. 启动应用

```bash
python server.py        # 或双击 start.bat 一键启动
```

访问 http://localhost:8501（启动后浏览器自动打开）

首次打开请在左侧侧边栏选择 LLM 提供商与模型，点击「🔗 测试连接」确认就绪后即可对话。

> **模型名选不到？** 模型下拉框已内置「✏️ 自定义模型名…」选项——选它后可直接输入任意模型名（如 `gpt-4o`、`glm-4.5`、`qwen2.5:32b`、自建网关上的模型 ID）。预设列表只是常用推荐，不影响使用列表外的模型；Ollama/LM Studio 会自动列出本地已安装/已加载的模型。

### 4. 使用本地 Ollama（可选，免费）

1. 安装 [Ollama](https://ollama.com)
2. 拉取对话模型：`ollama pull qwen2.5:7b`
3. 拉取 embedding 模型：`ollama pull nomic-embed-text`
4. 启动应用后，在侧边栏选择 **Ollama (本地)** 提供商

### 5. 使用本地 LM Studio（可选）

1. 安装 [LM Studio](https://lmstudio.ai)，在 **Developer** 标签页开启 Local Server
2. 加载对话模型；如需知识库检索，另加载一个 embedding 模型（如 `nomic-embed-text` / `bge` 系列）
3. 在设置页确认 `LMSTUDIO_BASE_URL`，其余留空可自动发现已加载模型

## 配置说明

所有配置通过项目根目录 `.env` 文件加载（复制自 `.env.example`），已填写的 Key 才会在界面中启用对应提供商。

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `DEFAULT_LLM_PROVIDER` | `openai` | 默认 LLM 提供商 |
| `DEFAULT_EMBEDDING_PROVIDER` | `openai` | 默认 embedding 提供商 |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | — | OpenAI（可指向任意兼容端点） |
| `ZHIPU_API_KEY` | — | 智谱 GLM |
| `DASHSCOPE_API_KEY` | — | 通义千问（DashScope 兼容模式） |
| `DEEPSEEK_API_KEY` | — | DeepSeek |
| `CUSTOM_API_BASE_URL` / `CUSTOM_API_KEY` / `CUSTOM_API_MODEL` | 空 | 自定义 OpenAI 兼容 API（任意中转/自建网关），Base URL 自动补全 `/v1`；也可在侧边栏选择「自定义 API」直接填写，即时生效 |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://localhost:11434` / `qwen2.5:7b` | Ollama 本地对话模型 |
| `OLLAMA_EMBEDDING_MODEL` | 留空用 `nomic-embed-text` | Ollama embedding 模型 |
| `LMSTUDIO_BASE_URL` / `LMSTUDIO_MODEL` | `http://localhost:1234` | LM Studio 本地服务 |
| `LMSTUDIO_EMBEDDING_MODEL` | 留空自动识别 | LM Studio embedding 模型（含 embed/bge/e5 等关键词自动发现） |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `500` / `50` | RAG 文档切分参数 |
| `TOP_K` | `5` | 向量检索返回条数 |
| `MAX_REACT_ITERATIONS` | `10` | 单轮 ReAct 最大迭代次数（调小可加快响应，见性能优化） |
| `MAX_TOKEN_BUDGET` | `8000` | 上下文 Token 预算，超限强制终止 |
| `CHROMA_PERSIST_DIR` | `./data/chroma_db` | ChromaDB 持久化目录 |
| `UPLOADS_DIR` / `CACHE_DIR` | `./data/...` | 上传/缓存目录 |

### Embedding 回退链

对话 LLM 与 embedding 独立配置。DeepSeek 无 embedding 服务，知识库检索会按以下回退链自动选择可用的 embedding 提供商：**LM Studio → Ollama → OpenAI → 智谱**（本地免费优先，云端需已配置 Key）。

**中文资料建议使用中文/多语言 embedding 模型**（英文为主的模型对中文论文向量化质量一般）：
- Ollama：`ollama pull bge-m3`，然后 `.env` 设置 `OLLAMA_EMBEDDING_MODEL=bge-m3`
- LM Studio：加载 bge/e5/gte 系列模型即可，系统会在已加载模型中自动识别（关键词已内置 `embed/bge/e5/gte/minilm/sentence`）

### RAG 切片与检索参数（已按中文论文场景调优）

| 参数 | 旧值 | 新值 | 说明 |
|------|------|------|------|
| `CHUNK_SIZE` | 500 | **800** | 中文 800 字符 ≈ 400~500 tokens，语义块更完整，避免段落被拦腰切断 |
| `CHUNK_OVERLAP` | 50 | **100** | 相邻块重叠，跨块语义不丢 |
| `TOP_K` | 5 | **8** | 召回 Top-8 相关片段，给 LLM 更充分的证据 |

> 调优依据：RAG 增强策略（bge 中文 embedding + Top-8 召回）在中文文献摘要任务中被验证有效。修改 `.env` 中 `CHUNK_SIZE`/`CHUNK_OVERLAP`/`TOP_K` 可覆盖默认值，重启生效。

## 项目结构

```
├── server.py               # Web 服务器（FastAPI：聊天 SSE / 研究任务 / 报告 / 知识库 / 设置 API + 静态前端）
├── web/                    # v2.0 原生前端（纯 HTML/JS，无框架、零重载）
│   ├── index.html          #   页面骨架（侧边栏 + 四页视图）
│   ├── app.js              #   全部前端逻辑（导航 / SSE 流式对话 / 研究轮询 / 报告管理 / 知识库）
│   └── style.css           #   玻璃拟态样式
├── config/                 # 配置管理
│   ├── settings.py         #   pydantic-settings 全局配置（.env 入口）
│   ├── llm_config.py       #   各提供商模型/上下文窗口/能力表
│   └── prompts.py          #   ReAct/研究员/分析员/总结员/质检/摘要提示词
├── llm/                    # LLM 层
│   ├── factory.py          #   工厂：多提供商创建 + 实例缓存
│   ├── openai_llm.py       #   OpenAI 兼容协议统一实现（含 Function Calling）
│   └── embedding.py        #   Embedding 提供商（自动回退链 + 短超时探测）
├── tools/                  # 工具系统（注册表模式）
│   ├── base.py             #   工具基类 + TOOL_REGISTRY
│   ├── web_search_cn.py    #   国内搜索（百度优先，必应 RSS 兜底）
│   ├── academic_search.py  #   学术文献检索（Crossref 被引 + 万方）
│   ├── paper_ingest.py     #   论文入库（Unpaywall OA → PDF → ChromaDB）
│   ├── search_tool.py      #   DuckDuckGo 搜索（可选，国外网络可用）
│   ├── web_scraper.py      #   网页正文抓取（requests + trafilatura）
│   ├── pdf_parser.py       #   PDF 解析（PyMuPDF）
│   ├── knowledge_base_tool.py  # 知识库检索工具（供 Agent 调用）
│   └── vector_store.py     #   ChromaDB 封装（显式 embeddings）
├── memory/                 # 记忆
│   ├── short_term.py       #   滑动窗口 + token 计数 + LLM 摘要压缩
│   ├── long_term.py        #   长期记忆
│   └── manager.py          #   统一记忆接口
├── agents/                 # ReAct + 三角色 Agent
│   ├── react_agent.py      #   Reason→Act→Execute→Observe 核心循环
│   ├── researcher_agent.py #   研究员（收集资料）
│   ├── analyst_agent.py    #   分析员（综合/矛盾检测/RAG 补充）
│   └── summarizer_agent.py #   总结员（生成结构化报告）
├── rag/                    # RAG 链路
│   ├── document_loader.py  #   PDF/TXT/MD/HTML/URL 加载
│   ├── text_splitter.py    #   文档切分
│   ├── embedder.py         #   向量化
│   └── retriever.py        #   检索 → LLM 重排 → 上下文生成
├── orchestration/          # LangGraph 多智能体编排
│   ├── state.py            #   共享状态契约
│   ├── supervisor.py       #   路由决策 + 质量检查
│   └── graph.py            #   工作流图构建与执行
├── utils/                  # 日志 / 安全防护 / 重试 / token 计数 / 报告导出
├── data/                   # 持久化数据（chroma_db / reports / uploads / cache）
└── tests/                  # 单元测试（131 项，含 server.py API 层 20 项）
```

## 多智能体研究工作流

### 对话页回答模式（快速 vs 深度）

对话页「⚙️ 工具配置」新增**回答模式**选择：

| 模式 | 行为 | 适用 |
|------|------|------|
| ⚡ 自动（默认） | 规则判断：研究意图问题（含"研究/调研/综述/最新进展/对比分析/论文/数据"等关键词）→ 走深度检索；普通问题、寒暄 → 直接回答（一次调用，不传工具 schema，快且省 token） | 日常混合使用 |
| 🔍 深度检索 | 始终走 ReAct 工具流程（网页 + 学术检索 + 抓取） | 明确需要查资料、可溯源回答 |
| 💬 纯聊天 | 始终直接回答，不调用任何工具 | 闲聊、翻译、创意写作 |

> 自动模式的规则是保守的：判定为普通问题时直接回答；判定为研究问题时进入深度检索（ReAct 第一轮若模型认为无需工具也会直接作答，不会强制搜索）。

### 研究员深度检索规则（有深度也有效率）

研究员提示词新增"深度检索规则"：每个子主题**至少深入 1 篇重点来源**（优先被引较高或最相关的期刊论文，用 `web_scraper` 抓全文细节 / 对 OA 论文用 `paper_ingest` 下载提取要点），其余来源保留题录+摘要——保证报告有具体方法、数据、案例细节，同时避免对所有来源都抓全文造成无效消耗。

### 并行预检索（研究提速）

研究页「高级配置 → ⚡ 并行预检索」默认开启：检索计划生成后，自动从计划中提取 3 组关键词，**并行**执行学术检索 + 网页搜索（最多 4 线程），把初始资料直接注入研究流程。研究员基于初始资料直接进入"深挖重点来源"阶段，省去逐轮串行搜索的 3~5 次 LLM 决策调用——**研究总耗时显著缩短，检索覆盖面更全**。关闭该选项则退回纯 ReAct 串行检索。

### 报告导出与历史

- 报告区提供 **💾 Markdown** 与 **📄 Word (.docx)** 两种下载（Word 由内置轻量转换器生成，标题/列表/引用/粗体/链接均保留）
- 每次研究生成的报告自动保存到 `data/reports/`（Markdown），刷新页面不丢
- 报告区底部「📂 历史报告」列出本地全部报告，随时回看下载
- **基于报告追问**：报告生成后可直接输入追问（如"第三部分展开讲讲"），系统带报告全文回答，并沿用 [n] 引用编号

### 研究后自动论文入库

研究页「高级配置 → 📥 研究后自动入库 OA 论文」默认开启：研究完成后自动提取报告中出现的 DOI，尝试下载前 2 篇开放获取论文全文入库（需向量库可用），之后可在「知识库」选择 papers 集合全文问答。非开放获取的论文会提示跳过，不影响研究结果。

### 一键启动与设置记忆

- **双击 `start.bat` 启动**：自动检查 Python/依赖、检测 8501 端口占用、打开浏览器并启动服务（依赖缺失时自动安装）
- **设置持久化**：侧边栏选择的提供商/模型/自定义模型名自动保存到 `data/ui_prefs.json`，重启应用后自动恢复，无需每次重新配置

```
检索计划（LLM 先规划子主题/关键词/时间范围，失败自动降级）
   │
Supervisor（路由）
   │
   ├─ 寒暄/短句 ──▶ Direct Answer（直接回答，结束）
   │
   └─ 研究类问题 ──▶ 研究员（ReAct 循环：web_search_cn 网页 + academic_search 学术文献
                        + paper_ingest 论文入库 + web_scraper 抓取 + 知识库；
                        每个子主题深挖 1 篇重点来源）
                        │
                        ▼
                    分析员（多来源对比、矛盾检测、RAG 补充）
                        │
                        ▼
                    总结员（生成结构化 Markdown 报告，正文 [n] 引用标注 + 参考文献列表）
                        │
                        ▼
                  质量检查 ──通过──▶ 结束
                        │
                        └──未通过（≤2 次）──▶ 回到研究员
```

### 学术研究闭环（借鉴开源项目 Paper-Agent / SPAR / PaperQA2）

| 环节 | 实现 | 借鉴来源 |
|------|------|----------|
| 检索计划先行 | 研究开始前 LLM 生成子主题/关键词/时间范围，按计划组织多路检索 | SPAR Query 理解、Paper-Agent SearchAgent |
| 多源学术检索 | `academic_search`：Crossref（国际期刊+中文期刊，含**被引次数**识别重点论文）+ 万方（中文） | Paper-Agent 三源检索 |
| 文献入库精读 | `paper_ingest`：DOI → Unpaywall 发现 OA 全文 → 下载 PDF → 解析 → 存入 ChromaDB「papers」集合，可全文问答 | Paper-Agent 渐进式阅读 |
| 报告引用标注 | 报告正文每处关键论断标注 [n]，文末参考文献列表（标题/链接/DOI） | PaperQA2 in-text citations、STORM |

> 说明：OpenAlex / Semantic Scholar / DBLP / arXiv 等学术 API 在国内网络下不稳定（实测被限流或连接重置），故采用国内实测可用的 Crossref + Unpaywall + 万方组合，均免费、无需 Key。

## 性能优化（网页卡顿排查与提升）

如果你遇到「网页打开/操作很卡」的情况，请按以下顺序排查。下表列出的根因均已在项目代码中修复（重启应用后生效），剩余为使用与部署建议。

### 已修复的根因（本项目代码已内置）

| 根因 | 表现 | 修复方式 |
|------|------|----------|
| **首屏加载全部重依赖**：chromadb（导入约 8 秒）与 openai（约 2.4 秒）在页面首次打开时全量加载，主要因为侧边栏一进来就初始化向量库 | 冷启动首次打开页面要等十几秒甚至更久 | 侧边栏不再强制初始化向量库，进入「知识库」页或启用 RAG 时才加载 chromadb；LLM 工厂延迟导入 openai（`ui/components/sidebar.py`、`llm/factory.py`、`ui/pages/knowledge_page.py`） |
| **四页 tabs 全量渲染**：`st.tabs` 下每次交互（切页/点按钮/输入）都会重跑 4 个页面的全部代码，包括反复初始化向量库 | 任何操作都明显卡顿 | 改为侧边栏导航，每次只渲染当前页（`ui/app.py`） |
| **Embedding 探测无超时 + 失败不缓存**：embedding 不可用时，每次 rerun 都重走完整回退链；openai SDK 默认超时 600 秒，本地服务无响应会长时间阻塞 | 页面长时间转圈、假死 | 本地服务探测超时 3s / 云端 10s；失败后 10 秒冷却窗口内不再重试（`llm/embedding.py`、`ui/components/sidebar.py`） |
| **LM Studio 模型自动发现失效且可能阻塞**：发现逻辑在 client 创建前调用（异常被吞，永远发现不到模型），且用 60 秒超时 client 探测 | 选本地服务后模型列表为空 / 切换时卡顿 | 先创建 client 再做发现；探测改用独立 3 秒短超时 client（`llm/openai_llm.py`） |
| **重复创建 LLM 客户端**：每次对话都新建 OpenAI client，本地服务还重复调用 `/v1/models` 自动发现 | 对话启动慢 | 增加 provider+model 级实例缓存（`llm/factory.py`） |
| **长对话全量渲染**：历史消息无限增长，每次 rerun 重绘全部 | 对话越长页面越卡 | 超过 40 条自动折叠早期消息（`ui/pages/chat_page.py`） |
| **研究报告区全页刷新**：下载/复制报告触发整页 rerun，重新执行页面全部逻辑 | 点一次按钮卡顿数秒 | 报告展示区用 `@st.fragment` 局部刷新，下载/复制只重跑该片段（`ui/pages/research_page.py`） |
| **Windows 文件监视不稳定**：watchdog 异常导致页面反复自动重跑 | 页面无操作也会自己刷新 | `.streamlit/config.toml` 改用轮询监视，并关闭遥测 |

> 提示：这些修复属于代码改动，应用重启后自动生效，无需额外配置。若重启后仍卡顿，继续看下面的使用建议。

### 首屏加载时间参考（优化前后实测）

| 环节 | 优化前 | 优化后 |
|------|--------|--------|
| 页面脚本执行（AppTest 实测） | ~7.7s | ~1.2s |
| 首屏依赖导入（chromadb/openai/trafilatura 等） | ~13s（首屏全量） | <1s（按需延迟加载） |
| 首次进入知识库页（加载 chromadb + 初始化） | — | ~5s（一次性，之后复用单例） |

### 使用与部署建议

1. **冷启动属正常现象**：进程首次启动/首次打开页面需要导入依赖与初始化，实测约 2~5 秒；首次进入「知识库」页需加载 chromadb（约 5 秒），此后复用单例不再卡。**若超过 30 秒**，多半是杀毒软件实时扫描拖慢了 Python 模块导入
2. **把项目目录加入杀毒软件白名单**：Windows Defender 等实时扫描会逐个检查 chromadb/langgraph 导入的数百个文件，首屏时间可能被放大数倍；加入白名单后冷启动可显著加快
3. **优先使用云端模型**：本地 7B 模型推理每轮 2~10 秒，多轮 ReAct 会明显变慢；云端 API（如 DeepSeek / 智谱）速度快得多
4. **确保本地服务已启动**：使用 Ollama / LM Studio 前先启动服务并加载模型；未启动时页面每次交互都要等待连接探测失败（虽然已缩短到秒级，但仍建议启动）
5. **调小 `MAX_REACT_ITERATIONS`**：默认 10 意味着单轮最多 10 次 LLM 调用。日常问答调到 4~6 即可，研究任务再用大值
6. **调低检索参数**：`TOP_K` 从 5 降到 3、`CHUNK_SIZE` 用默认值即可，减少每次知识库检索的 embedding 请求
7. **升级依赖**：本项目依赖较宽松（`fastapi>=0.110`、`langgraph>=0.2`），建议定期 `pip install -U fastapi uvicorn langgraph chromadb openai`
8. **关掉浏览器无关扩展 / 用 Chrome 无痕模式访问**：部分扩展会拖慢 WebSocket 渲染
9. **部署到服务器**（可选）：

```bash
# 后台运行，监听 0.0.0.0 供局域网访问
python server.py --host 0.0.0.0 --port 8501
```

10. **长对话主动清空**：侧边栏「🗑️ 清空会话」可重置上下文；历史越长，每次重绘与 token 计算越重

## 降低 Token 消耗（省钱指南）

多智能体研究贵在「每轮迭代把全部工具返回 + 完整历史重发给 LLM」。以下优化已内置，另附使用建议：

### 已内置的 Token 优化（代码层面）

| 优化 | 说明 |
|------|------|
| **检索条数预算** | 研究页「高级配置 → 单次检索条数」默认 5，代码层强制 clamp（LLM 传再大也被限制在预算内），直接削减每轮工具返回体量 |
| **抓取正文截断** | `web_scraper` 默认最多返回 2000 字符（原 3000），去掉冗余正文 |
| **学术检索精简** | 每条文献只保留标题/作者/年份/期刊/摘要（≤400 字符）/DOI，丢弃冗余字段 |
| **ReAct 记忆滚动压缩** | 内存管理器在上下文接近 token 上限时自动对早期工具观察做 LLM 摘要压缩，不再完整携带全部历史原始检索结果（`memory/manager.py`） |
| **token 预算硬顶** | SafetyGuard 设置上下文 token 预算，超限强制终止并提示（`utils/safety.py`） |
| **防循环检测** | 相同工具 + 相同参数重复调用直接终止，避免无效消耗（`utils/safety.py`） |
| **检索计划提示** | 研究开始前 LLM 先规划子主题与关键词，让检索更聚焦，减少盲目多轮搜索 |

### 使用建议（用户可调）

1. **省钱模式**：最大迭代 4、单次检索条数 4，适合初步调研与本地 7B 模型
2. **全面调研**：最大迭代 6-8、单次检索条数 6-8，适合最终报告
3. **本地 7B 模型天然省钱**：0 元/次；缺点是输出详细度不如云端。混合用法：先本地快速跑通框架，正式报告切 DeepSeek-Flash 等云端模型
4. **敏感问题避免多轮追问**：一次把研究主题写清楚，检索计划越具体，LLM 轮询越少

> 实测参考：DeepSeek-Flash 完整学术调研输入约 120k tokens / 约 0.6 元；用上述默认参数（迭代 5、条数 5）通常可将输入压缩至 30-50k tokens，成本约降一半以上。注意实际费用以官方计费为准。

## 项目定位：什么时候用它、什么时候直接问 AI

这个项目**不是**普通 AI 对话的替代品——如果你只是"问个问题拿个答案"，直接问 AI（豆包/ChatGPT/DeepSeek 网页版）更快、更便宜、更顺滑。它的价值在普通 AI 给不了的三个场景：

| 维度 | 普通 AI 对话 | 本项目 |
|------|-------------|--------|
| 速度 | ✅ 秒级回答 | ⚠️ 多智能体研究需几分钟 |
| 单次成本 | ✅ 免费/会员 | ⚠️ 云端按量（本地 0 元） |
| **回答可溯源** | ❌ 凭训练数据，易幻觉、会编假来源 | ✅ 现场检索，正文 [n] 引用 + 真实 DOI，可点击验证 |
| **时效性** | ❌ 知识截止训练时间 | ✅ 现场检索最新资料 |
| **私有知识** | ❌ 不认识你的文档 | ✅ RAG 知识库，基于你的资料回答 |
| **数据隐私** | ❌ 数据上传云端 | ✅ 本地模型（LM Studio/Ollama）数据不出本机 |
| **自动化/定制** | ❌ 黑盒 | ✅ 工具链、提示词、流程全是代码，可扩展可复现 |

**使用判断：**
- 研究性问题（需要文献支撑、结论可验证）→ 用它
- 你的私有文档/论文问答 → 用它（普通 AI 做不到）
- 敏感/涉密数据 → 用它（本地模型跑）
- 一句话知识问答、闲聊、翻译、找灵感 → 直接用普通 AI，别用它

## QLoRA 微调 Qwen2.5-7B：可行性评估（实测硬件）

> 结论先行：**当前硬件下不建议做 7B 微调**。原因如下，供参考。

### 硬件门槛（本项目实机：RTX 4060 Laptop 8GB 显存 / 16GB 内存）

| 方案 | 显存需求 | 本机可行性 |
|------|---------|-----------|
| QLoRA 微调 Qwen2.5-**7B** | 12GB+（4bit + gradient checkpointing） | ❌ 8GB 勉强边缘，训练极易 OOM；Laptop GPU 功耗散热受限，预计十小时以上 |
| QLoRA 微调 Qwen2.5-**3B** | 6~8GB | ⚠️ 可尝试，但 3B 输出质量本就低于 7B，微调提升有限 |
| 继续用 7B 推理 + RAG | 推理 4~6GB | ✅ 当前方案 |

### 数据与收益评估

1. **需要领域标注数据**：微调要有与目标领域一致的"文献→摘要/问答"训练集（如 EduReport-Summarizer 的数据格式），通用研究助手场景没有现成数据，需自行构造和人工标注，成本不低
2. **收益不迁移**：引用中"ROUGE 提升 44%"来自**特定领域**（教育报告摘要、科技文献摘要）的评测；本项目是**多领域通用研究助手**，领域微调会降低其他主题的通用性（灾难性遗忘）
3. **当前瓶颈不在模型输出能力**：7B 报告空洞的历史根因是①输出被 max_tokens 截断（已修）②检索资料贫乏（已通过检索深度规则 + RAG 参数调优解决），这两个都不需要微调
4. **性价比对比**：微调 7B 的投入（数据、数小时训练、损伤通用性）远大于直接换云端模型做正式报告（DeepSeek-Flash 约 0.6 元/次）

### 如果仍要微调（保留的路线）

- 工具：LLaMA-Factory（GUI 友好）或参考 EduReport-Summarizer 的 QLoRA 流水线
- 起步建议：先微调 **Qwen2.5-3B-Instruct**（8GB 可跑），验证数据质量后再考虑升级
- 数据格式：`{"instruction": "请根据检索到的文献片段生成报告章节摘要", "input": "<文献片段>", "output": "<期望摘要>"}`，300~1000 条起步
- 预期时间：3B 全量准备 + 训练约 2~4 小时；7B 约 10 小时以上

> 当前最务实的路径：**RAG 调优（已落地）+ 检索深度（已落地）+ 需要高质量正式报告时切云端模型**。微调等有 12GB+ 显存或明确固定领域需求时再上。

## 运行测试

```bash
pip install pytest pytest-mock
pytest tests/ -v
```

当前共 **131 项测试**全部通过（无网络依赖，LLM/搜索/入库均 mock）：
- 核心：LLM 工厂、OpenAI 兼容 LLM（max_tokens 透传 / 429 限流 / Function Calling 降级）、记忆（短期/压缩）、编排图（含 max_iterations/detailed 透传）、ReAct 循环、工具注册与执行、SafetyGuard
- 学术：Crossref 检索（被引/OA 标记/摘要清理/失败降级）、中文站点解析、论文入库（DOI 提取/非 OA 提示/下载失败/完整入库链路）
- 界面逻辑：研究页工具（预算 clamp/关键词/DOI 提取/预检索）、对话快速问答路由、总结员（详细版提示词/引用池/失败降级）
- 导出与持久化：Markdown→Word 转换结构、UI 偏好读写与损坏容错
- **API 层（v2.0）**：`tests/test_server_api.py` 20 项——健康检查/提供商与模型列表/测试连接/设置保存（含 .env 写入）/聊天 SSE（快速与深度两路）/研究任务启动与轮询/报告列表与失败过滤/报告下载与目录穿越防护/报告删除/知识库集合管理与上传/检索

## v2.0 前端（性能专项）

| 问题 | 解决方案 |
|------|----------|
| **页面切换卡顿（Stremlit 每次切页全量重渲染 + 网络回包）** | 前端重写为**纯 HTML/JS 单页应用**（`web/`），四个页面预置在 DOM 中，切换只改显隐，零网络请求、零重渲染；实测点击切换瞬时完成 |
| 后端仍承担全部业务 | `server.py`（FastAPI）提供统一 API：聊天 SSE 流式 / 研究任务后台线程 + 轮询 / 报告下载删除 / 知识库管理；复用原 LLM 工厂、LangGraph 编排、RAG、搜索工具，业务逻辑零改动 |
| 失败报告残留 | 沿用失败标记过滤（429/API 调用失败/报告生成失败自动过滤），历史列表只显示有效报告 |
| 历史报告管理 | 网页端「🗑️ 删除」直接移除 `data/reports/` 对应文件；「下载」支持 Markdown / Word |

## 故障排查（FAQ）

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| 研究报告如何下载 / 复制 | 报告生成后才有导出区 | 研究完成后，报告区下方出现「💾 下载 Markdown」与「📄 下载 Word」按钮；st.code 代码块右上角自带复制按钮。报告自动保存到 `data/reports/`，刷新不丢，可从「📂 历史报告」随时下载 |
| 没有下载按钮 | 本轮研究未生成报告（研究失败或页面刷新丢失） | 重新执行一次研究；下载区在生成报告后自动出现 |
| 本地模型（7B 以下）报告太短 / 不详细 | ① LLM 默认输出上限低（本地服务通常 512 tokens），报告被截断；② 提示词要求笼统 | ① 已在代码中修复：报告生成固定 `max_tokens=8192`，对话默认 4096；② 研究页「报告详细度」选「📕 详细版」：固定章节骨架（摘要/背景/现状/应用/挑战/结论/参考文献）+ 800~1500 字篇幅要求 + 逐条融入检索资料。若仍偏短，可在 LM Studio 中调大模型的 max tokens 上限 |
| 侧边栏提示「向量存储不可用」 | 未配置云端 Key 且本地无 embedding 模型 | 设置页配置任意云端 Key，或 `ollama pull nomic-embed-text` 后重启应用 |
| 「测试连接」显示红色/无法连接 | 服务未启动 / Key 无效 / 网络问题 | 本地服务先启动；云端检查 Key 与额度 |
| 对话回复「模型不支持工具调用」 | 本地小模型不支持 Function Calling | 系统已自动降级为纯对话，可直接用；或换支持工具调用的模型 |
| 页面卡顿 / 转圈 | 见上方「性能优化」章节 | 按章节逐项排查 |
| 修改 `.env` 后不生效 | pydantic-settings 启动时读取一次 | 重启应用；设置页保存后同样需要重启 |
| 知识库检索无结果 | 未上传文档 / embedding 未配置 / 相似度低于阈值 | 先上传文档，检查「测试检索」返回；`TOP_K` 与阈值相关参数在 `.env` |
| 搜索工具报错 / 无结果 | 国内网络无法访问 DuckDuckGo；目标网站反爬或不可达 | 系统默认使用 `web_search_cn`（百度优先、必应兜底）与 `academic_search`（学术文献），无需 Key；对话页「⚙️ 工具配置」勾选即可。抓取失败提示说明原因，可换搜索到的其他来源 |
| 如何检索学术文献 / 论文 | 需要研究性问题的文献支撑 | 研究页与对话页默认启用 `academic_search`：国际期刊走 Crossref（含收录的中文学术期刊、被引次数），中文论文走万方。返回标题/作者/年份/期刊/摘要/DOI。带 📄开放全文 标记的论文可用 `paper_ingest` 下载全文入库，之后到「知识库」选择 papers 集合全文问答。知网因反爬与付费墙无法直连，其收录内容可通过上述来源获取 |
| 论文入库失败（无法下载） | 论文非开放获取（付费墙）或下载源反爬 | `paper_ingest` 仅支持开放获取全文（Unpaywall 发现）。付费论文可在 doi.org 查看题录，或换其他 OA 论文 |
| 提示「API 限流（请求过于频繁）」/ 429 | 提供商分钟级请求限制（如智谱免费模型 RPM） | 已内置友好提示与退避，不再无效重试；稍等 1~2 分钟，或在侧边栏换模型/提供商（如 glm-4-air、DeepSeek、自定义 API） |

## 技术栈

| 模块 | 技术 |
|------|------|
| LLM 接入 | openai（兼容协议复用，覆盖 6 家提供商） |
| Agent 框架 | langgraph |
| 向量数据库 | chromadb（本地持久化） |
| 文档解析 | PyMuPDF, trafilatura |
| 搜索 | web_search_cn（百度/必应，国内可用）、academic_search（Crossref/万方）、duckduckgo-search（可选） |
| Web 服务 | fastapi + uvicorn（v2.0 主入口） |
| 配置 | pydantic-settings |

## 安全与隐私

- 所有 API Key 仅存于本地 `.env`（已被 `.gitignore` 忽略），经设置页保存时同样写入本地文件，不会上传
- 向量库与上传文档全部保存在本地 `data/` 目录
- ReAct 循环内置：Token 预算强制终止、重复 Action 循环检测、长对话摘要压缩，防止失控调用消耗额度
- **破坏性命令双层防护**：
  - 提示词层：`config/prompts.py` 的 `SAFETY_CONSTRAINTS` 约束所有 ReAct/研究员 Agent
  - 运行时层：`utils/safety.py` 的 `SafetyGuard.check_destructive` 在 `ReActAgent._execute_tool` 中对 `dangerous=True` 的工具做硬拦截
  - 禁止自动执行 `rm`/`rmdir`/`git push -f`/`mkfs`/`format`/`格式化`/删除文件类破坏性命令；高危删除操作须用户明确确认后才能运行

## 版权声明

本项目**未采用任何开源许可证**，依据著作权法默认「保留所有权利（All Rights Reserved）」：

- 允许依据 GitHub 服务条款**查看**本仓库代码
- **未经作者书面授权，禁止任何商业用途**，禁止再分发、转载、修改后发布
- 如需使用、合作或授权，请联系作者
