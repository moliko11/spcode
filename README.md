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

推荐先修改仓库根目录的 `agent.config.yaml`，再启动 CLI 或脚本。默认情况下，runtime 会自动读取这个文件；也可以通过环境变量 `AGENT_CONFIG=/path/to/agent.config.yaml` 指向其他配置文件。

安装依赖：

```powershell
uv sync
```

最小启动步骤：

1. 打开 `agent.config.yaml`
2. 修改 `model.url`、`model.name`，必要时修改 `model.api_key`
3. 如果你想限制 agent 可操作目录，修改 `runtime.workspace_root`
4. 运行 `uv run agent` 或 `uv run python main.py chat "hello"`

默认配置文件示例：

```yaml
model:
     url: http://127.0.0.1:11434/v1
     name: qwen3
     api_key: EMPTY
     temperature: 0.5

runtime:
     workspace_root: ./runtime_data/workspace
     loaded_tools:
          - get_current_time
          - calculator
          - file_read
          - grep
          - tool_search
     short_memory_turns: 8

budget:
     max_steps: 20
     max_tool_calls: 20
     max_state_tool_calls: 120
     max_read_tool_calls: 120
     max_network_tool_calls: 30
     max_high_risk_tool_calls: 20
     max_seconds: 160

skills:
     roots:
          - ./skills
```

说明：

- `agent.config.yaml` 中的相对路径按配置文件所在目录解析，不依赖当前 shell 的工作目录。
- CLI 默认 provider 是 `openai_compatible`，因此只要模型端点兼容 OpenAI 接口，通常只改这个 YAML 就能跑。
- 如果你切换到云 provider，仍然需要补充对应环境变量，例如 `QWEN_API_KEY`、`DEEPSEEK_API_KEY`。

运行一次聊天：

```powershell
uv run python main.py chat --provider mock "hello"
```

进入 CLI 工作台：

```powershell
uv run agent
```

工作台普通输入会走流式 chat；输入 `/` 会显示 slash 命令补全菜单，可继续输入字符过滤并用方向键选择。常用命令包括 `/history [limit]` 查看当前 session 历史、`/clear` 开启新 session、`/status` 查看状态、`/plan` 和 `/run` 管理计划执行。

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

Phase 5 起，推荐把仓库根目录的 `agent.config.yaml` 作为主配置入口；`packages/runtime/config.py` 仍然保留默认值和常量定义，但日常使用优先改 YAML。

当前支持这些配置段：

- `model.url` / `model.name` / `model.api_key` / `model.temperature`
- `runtime.workspace_root` / `runtime.loaded_tools` / `runtime.short_memory_turns`
- `budget.max_steps` / `budget.max_tool_calls` / `budget.max_state_tool_calls`
- `budget.max_read_tool_calls` / `budget.max_network_tool_calls` / `budget.max_high_risk_tool_calls` / `budget.max_seconds`
- `skills.roots`

如果你需要针对不同机器维护不同配置，有两种方式：

- 直接在仓库根目录修改 `agent.config.yaml`
- 使用 `AGENT_CONFIG` 指向另一份配置文件

对开发者来说，`build_runtime()` 和 `build_llm()` 现在都支持 `config_path` 参数，可以显式指定配置文件路径。

常用环境变量：

- `AGENT_WORKSPACE`：agent 可操作的工作目录，默认是 `./runtime_data/workspace`。
- `AGENT_CONFIG`：指定要加载的 `agent.config.yaml` 路径。
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
     -> close any dangling previous user turn
     -> recall memory
     -> apply autonomy policy
  -> build system prompt
     -> model decides direct answer / todo / plan mode / tool execution
     -> maybe call tools in parallel
  -> checkpoint
  -> feed tool result back
     -> verify or replan managed tasks when needed
  -> final answer
  -> remember run
```

普通 `chat` 会先按输入和近期会话判断本轮模式：简单问题直接答；复杂行动请求会倾向先用 `todo_write` 暴露执行计划；只规划请求会进入 plan mode 并阻断副作用工具；用户批准上一轮计划后会按对话中的计划继续执行。对于托管 workflow 任务，可用 `task_verify` 记录验收/测试证据，用 `task_replan` 对失败任务局部追加或替换后续任务。

为避免旧任务污染新输入，runtime 在每轮 chat 开始前会检查当前 session 是否以未回复的 user 消息结尾；如果存在，会先写入一条 assistant 终止消息来闭合上一轮。正常完成、失败和取消路径也都会落盘 assistant 消息，让后续请求总是从清晰的 turn 边界开始。

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
- `todo_write`
- `enter_plan_mode`
- `exit_plan_mode`
- `get_current_time`
- `calculator`

动态工具包括：

- `task_create`
- `task_update`
- `task_list`
- `task_output`
- `task_verify`
- `task_replan`
- `task_stop`
- `skill`
- `mcp`

安全策略：

- 工作区路径必须限制在 `runtime.workspace_root` 指定目录内；未显式配置时默认等价于 `AGENT_WORKSPACE` / `./runtime_data/workspace`。
- 写操作必须通过审批策略。
- bash 默认高风险，必须审批。
- plan mode 开启后，executor 会阻断写文件、bash、网络等副作用工具，只允许继续规划、读取上下文或退出 plan mode。
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

- 完善 CLI UI 工作台体验，提供类似 Claude Code / Codex 的流式对话、计划、运行、审批和状态入口。
- 完善普通 chat 的自主规划、验收和失败重规划策略。
- 修复持久化 ID 的路径安全问题。
- 让 runtime 失败状态统一落盘，避免异常后 checkpoint 停在 running。
- 隔离并行 orchestrator 中的 per-run 状态。
- 给 web fetch/search 增加 SSRF 防护。
- 审计日志脱敏。
- bash 工具补充 changed_files 追踪。
- README、docs 和代码注释统一编码。

中期目标：

- Plan-only / guided / auto-edit / full-auto-sandbox 等执行模式的用户可配置化。
- 更完整的 Plan validator、verifier 和 replanner 策略。
- Workflow-level evidence 和 artifact 记录的 UI 展示。
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
