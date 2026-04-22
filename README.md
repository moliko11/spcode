# Agent Leaning

一个面向个人生产环境的通用智能体项目。

项目目标不是只做一个代码助手，而是做一个可以长期服务个人工作的 AI agent runtime：既能完成代码阅读、修改、测试、审查和调研，也能处理通用任务；同时把多策略编排、群体协作、代币预算控制、人工审批工作流和时间旅行调试作为内置能力，而不是外围脚本。

## 项目定位

Agent Leaning 的核心定位是：

- 个人生产型人工智能代理：优先服务个人真实工作流，强调可恢复、可审计、可控和可持续使用。
- 通用智能体：不仅面向代码任务，也支持文件、搜索、网页、shell、记忆、规划和外部工具能力。
- 多策略编排系统：根据任务类型在直接回答、工具调用、计划执行、审批暂停、恢复执行等策略之间切换。
- 群体协作基础设施：以 planner、orchestrator、step executor、tool registry 为基础，逐步扩展到 specialist agent 和 team workflow。
- 可控执行环境：所有高风险写操作、shell 操作和长期任务都应经过权限、guardrail、预算和审计系统约束。
- 可调试运行时：通过 checkpoint、event log、plan run 和 resume 机制支持“时间旅行调试”，可以回看和恢复 agent 的中间状态。

## 当前能力

当前代码已经具备这些主要模块：

- LLM 适配：`packages/model_loader.py` 支持 OpenAI-compatible 后端和 fallback 模型链。
- Runtime loop：`packages/runtime/agent_loop.py` 实现单轮 agent 决策、工具调用、checkpoint 和恢复。
- 工具注册与执行：`packages/runtime/registry.py`、`packages/runtime/executor.py` 负责工具 schema、权限、审批、重试和结果归一化。
- 工具集：文件读写编辑、glob、grep、bash、web search、web fetch、tool search、skill、MCP。
- 权限与审批：写文件、编辑文件和 bash 默认要求人工审批。
- 预算控制：按最大 step、最大工具调用数和最大运行时间限制 agent。
- 记忆系统：`packages/memory` 支持 recall、remember、运行摘要和上下文压缩。
- 规划系统：`packages/planner` 支持把目标拆成带依赖关系的 task plan。
- 编排系统：`packages/orchestrator` 支持 plan run、step run、依赖调度、暂停审批和恢复。
- CLI 入口：`main.py` 提供 chat、plan、orchestrate、approve、resume、show-session、show-memory。

## 架构概览

```text
main.py
  -> packages/runtime
       config.py          运行时配置、路径、默认工具
       models.py          AgentState、ToolSpec、ToolResult、事件模型
       agent_loop.py      核心 agent 循环
       executor.py        工具执行器
       permission.py      权限与人工审批
       guardrail.py       参数、路径和结果校验
       budget.py          预算、重试、幂等缓存
       store.py           session/checkpoint 持久化
       message_builder.py 系统提示词和上下文构建
       bootstrap.py       runtime 装配入口

  -> packages/tools
       BashTool
       FileReadTool / FileWriteTool / FileEditTool
       GlobTool / GrepTool
       WebSearchTool / WebFetchTool
       ToolSearchTool
       SkillTool / MCPTool

  -> packages/planner
       planner.py         LLM 生成结构化计划
       scheduler.py       依赖调度
       store.py           plan 持久化
       models.py          TaskPlan / TaskStep

  -> packages/orchestrator
       orchestrator.py    plan -> wave scheduling -> step execution
       executor.py        step 到 AgentRuntime 的执行适配
       store.py           plan run 持久化
       models.py          PlanRun / StepRun

  -> packages/memory
       manager.py         recall / remember
       store.py           JSONL memory store
       compaction.py      上下文压缩
       summarizer.py      运行摘要
```

## 运行方式

安装依赖：

```powershell
uv sync
```

运行一次聊天：

```powershell
uv run python main.py chat --provider mock "hello"
```

生成计划但不执行：

```powershell
uv run python main.py plan --provider openai_compatible "review packages/runtime/executor.py"
```

规划并执行一个目标：

```powershell
uv run python main.py orchestrate --provider openai_compatible "给 runtime executor 补充测试"
```

恢复中断的 plan run：

```powershell
uv run python main.py resume <plan_run_id>
```

审批暂停中的 plan run：

```powershell
uv run python main.py approve <plan_run_id>
```

查看会话：

```powershell
uv run python main.py show-session --session-id demo-session
```

查看记忆：

```powershell
uv run python main.py show-memory --user-id demo-user
```

## 配置

主要配置在 `packages/runtime/config.py` 和 `.env` 中。

常用环境变量：

- `AGENT_WORKSPACE`：agent 可操作的工作目录，默认是 `./runtime_data/workspace`。
- `LOCAL_MODEL_URL`：本地 OpenAI-compatible 模型地址。
- `LOCAL_MODEL_NAME`：本地模型名称。
- `LOCAL_MODEL_API_KEY`：本地模型 API key。
- `QWEN_API_KEY`：启用 Qwen fallback/primary。
- `DEEPSEEK_API_KEY`：启用 DeepSeek fallback。
- `CURRENT_DATE`：注入给 agent 的当前日期，主要用于测试和调试。
- `CURRENT_TIMEZONE`：注入给 agent 的时区。

## 工作流

### Chat

`chat` 是单个 agent loop：

```text
user message
  -> recall memory
  -> build system prompt
  -> model decides
  -> maybe call one tool
  -> checkpoint
  -> feed tool result back
  -> final answer
  -> remember run
```

### Plan

`plan` 只生成结构化计划，不执行工具。它适合在高风险任务前先审查拆解方式。

计划包含：

- step id
- title
- description
- dependencies
- acceptance criteria
- suggested tools

### Orchestrate

`orchestrate` 会先生成计划，再按依赖关系执行 ready steps。

设计目标是：

- 支持复杂任务拆解。
- 支持可并行的 wave 调度。
- 支持 step 级 checkpoint。
- 支持人工审批暂停。
- 支持进程中断后的恢复。
- 支持最终汇总。

### Approval

写文件、编辑文件和 bash 属于高风险工具，默认触发人工审批。

审批时可以：

- approve：按原参数执行。
- reject：拒绝并停止当前执行。
- edit：修改工具参数后再批准。

### Time Travel Debugging

项目里的“时间旅行调试”主要由这些机制组成：

- session store：保存用户和 assistant 对话。
- checkpoint store：保存每个 run 的 AgentState。
- event bus：记录运行开始、模型输出、工具选择、审批、工具完成等事件。
- plan store / plan run store：保存 workflow 级计划和执行状态。
- resume：从等待人工、工具已执行、运行中断等状态恢复。

目标是让 agent 的每一步都能被回放、诊断和恢复，而不是只留下最后一句回答。

## 工具与安全边界

默认工具集包括：

- `file_read`
- `file_write`
- `file_edit`
- `glob`
- `grep`
- `list_dir`
- `web_search`
- `web_fetch`
- `bash`
- `tool_search`
- `get_current_time`
- `calculator`

动态工具包括：

- `skill`
- `mcp`

安全策略：

- 工作区路径必须限制在 `AGENT_WORKSPACE` 内。
- 写操作必须通过审批策略。
- bash 默认高风险，必须审批。
- 工具参数进入 guardrail 校验。
- 运行过程写入 audit log。
- budget controller 限制无限循环和过度工具调用。

## 测试

运行完整测试：

```powershell
uv run python -m pytest
```

运行部分测试示例：

```powershell
uv run python -m pytest tests/test_core_file_tools.py tests/test_core_search_tools.py
```

## 开发路线

近期重点：

- 修复持久化 ID 的路径安全问题。
- 让 runtime 失败状态统一落盘，避免异常后 checkpoint 停在 running。
- 隔离并行 orchestrator 中的 per-run 状态。
- 给 web fetch/search 增加 SSRF 防护。
- 审计日志脱敏。
- bash 工具补充 changed_files 追踪。
- README、docs 和代码注释统一编码。

中期目标：

- Plan-only / guided / auto-edit / full-auto-sandbox 等执行模式。
- Plan validator、verifier 和 replanner。
- Workflow-level evidence 和 artifact 记录。
- Project instructions 加载，类似 AGENTS.md。
- 更稳定的长期记忆和语义检索。
- 任务级资源锁，避免并行编辑冲突。

长期目标：

- subagent 和 team 协作。
- reviewer / tester / researcher / coder 等角色化 agent。
- workflow UI。
- plugin/tool marketplace。
- notebook 编辑。
- 代码索引、符号分析和调用图。

## 设计原则

- 先可控，再自动化。
- 先可恢复，再长任务。
- 先可审计，再并行。
- 工具能力必须受权限、路径、预算和审批约束。
- 计划必须可以被人审查。
- 失败应该产生可诊断状态，而不是静默丢失。
- 个人习惯优先，但架构保持通用化。

## 项目状态

这是一个正在演进中的个人 agent runtime。当前已经具备核心骨架和较多测试，但仍处于生产化前的打磨阶段。适合继续作为个人智能体实验平台、代码 agent runtime 和 workflow orchestration 原型推进。
