# Web 前端与 CLI 配套实现调研

本文面向当前 Agent Leaning 代码库，调研如何为现有 agent runtime、planner、orchestrator、memory 和 approval 能力补齐配套的 Web 前端与 CLI。结论不是另起一套产品，而是在现有 `packages/runtime`、`packages/planner`、`packages/orchestrator` 之上增加稳定的应用服务层，让 Web 和 CLI 共用同一批用例、状态模型和审批协议。

## 当前基础

仓库已经具备这些可复用能力：

- `AgentRuntime.chat()`：单轮或多轮 session 的 agent 执行入口，支持 checkpoint、tool call、人工审批暂停和 resume。
- `Planner.create_plan()`：把目标拆成结构化 `TaskPlan`。
- `Orchestrator.run()` / `resume()` / `recover()`：执行 plan run，支持 wave 并行、步骤隔离 session、审批暂停和跨进程恢复。
- `FileSessionStore`、`FileCheckpointStore`、`PlanStore`、`PlanRunStore`、memory store：当前以本地文件持久化。
- `EventBus`、`AgentEvent`、audit log：已经有事件模型，但现在主要用于日志和审计，没有对外实时订阅接口。
- `main.py`：已有 `chat`、`plan`、`orchestrate`、`approve`、`resume`、`show-session`、`show-memory` 命令。

当前缺口：

- CLI 直接组装 runtime/planner/orchestrator，业务逻辑和展示逻辑混在 `main.py`。
- 没有稳定的 API schema，Web 端无法安全地启动、观察、审批和恢复任务。
- 事件只写日志，前端无法实时看到 run/step/tool/approval 状态。
- plan、checkpoint、session、audit、memory 是文件存储，适合原型，但需要抽象接口以便后续迁移 SQLite/Postgres。

## 技术选型结论

建议分三层实现：

```text
packages/app_service
  chat_service.py
  plan_service.py
  run_service.py
  approval_service.py
  query_service.py
  schemas.py

packages/api
  fastapi_app.py
  routes/
  event_stream.py

packages/cli
  app.py
  render.py
  client.py

web/
  Next.js / React / TypeScript
```

核心原则：

- Web 与 CLI 都调用 `packages/app_service`，不要各自重新拼 runtime。
- Web API 用 FastAPI 暴露 REST + SSE。REST 负责命令式操作，SSE 负责运行事件流。
- 前端用 React/Next.js 或 Vite React 均可。若需要本地桌面式 dashboard，Vite React 更轻；若未来有登录、多页面、服务端渲染和部署需求，Next.js App Router 更合适。
- CLI 从 `argparse` 演进到 Typer + Rich。Typer 负责多子命令和类型化参数，Rich 负责表格、进度、live run view。
- 存储短期继续使用文件，先抽象 repository/service，再考虑 SQLite。

调研依据：

- FastAPI 官方文档支持 WebSocket；生产前端可用现代框架直接连接 WebSocket 后端。但本项目大多数状态是服务端单向推送，REST + SSE 更简单。
- FastAPI 官方 SSE 文档说明 SSE 使用浏览器原生 `EventSource` 支持的 `text/event-stream`，适合 AI chat streaming、实时通知、日志和可观测性；还能设置事件 `id` 支持断线续传。
- FastAPI `BackgroundTasks` 适合请求返回后的小型后台任务，但官方也提示重计算或多进程任务应使用 Celery 等队列。本项目 agent run 是长任务，应由显式 task manager 管理，而不是简单塞进 `BackgroundTasks`。
- TanStack Query 官方定位是管理 Web 应用 server state，提供缓存、同步、后台刷新、mutation 等能力，适合前端展示 run list、plan run、memory、session 等远端状态。
- Next.js App Router 官方文档支持 Server Components、Suspense 和 streaming。若采用 Next.js，首屏 dashboard 和历史记录可以走服务端数据获取，实时运行面板仍放在 Client Component 中消费 SSE。
- Typer 官方文档支持多子命令、类型转换、命令帮助和主 callback，适合替换当前增长中的 `argparse`。
- Rich 官方文档提供 table、progress、live display，适合 CLI 展示 plan step、tool event、审批请求和长任务运行状态。

参考链接：

- FastAPI WebSockets: https://fastapi.tiangolo.com/advanced/websockets/
- FastAPI Server-Sent Events: https://fastapi.tiangolo.com/tutorial/server-sent-events/
- FastAPI Background Tasks: https://fastapi.tiangolo.com/tutorial/background-tasks/
- TanStack Query: https://tanstack.com/query/docs
- Next.js App Router data fetching: https://nextjs.org/docs/app/getting-started/fetching-data
- Typer: https://typer.tiangolo.com/
- Rich progress/live/table: https://rich.readthedocs.io/en/latest/progress.html

## Web 前端实现方案

### 推荐信息架构

Web 前端第一阶段不要做营销页，直接做运行工作台：

- `Runs`：展示近期 agent run 和 plan run，按 `running`、`waiting_human`、`failed`、`completed` 过滤。
- `Chat`：输入任务，选择 provider、user_id、session_id，展示模型输出、工具调用和最终回答。
- `Plans`：生成计划、查看 DAG/依赖、启动 orchestrate。
- `Run Detail`：展示 timeline、step_runs、tool_results、checkpoint、runtime_messages 摘要、audit events。
- `Approvals`：集中处理 `waiting_human`，支持 approve、reject、edit arguments。
- `Memory`：查看 user memories、run summaries、transcripts、recall 命中。
- `Settings`：provider、workspace、budget、默认工具、审批策略，只展示可安全修改项。

### 前端技术栈

推荐两种路线：

1. 原型优先：`Vite + React + TypeScript + TanStack Query`
   - 启动快，目录简单。
   - 适合本地工作台，不需要 SSR。
   - 与 Python API 解耦清晰。

2. 产品化优先：`Next.js App Router + React + TypeScript + TanStack Query`
   - 多页面、认证、部署和服务端数据获取更完整。
   - 历史 run list、memory、settings 可以在 Server Component 获取。
   - 实时 run detail 仍需要 Client Component 订阅 SSE。

当前项目建议先用 Vite React，等 API 和交互稳定后再判断是否迁移 Next.js。原因是这个项目本质是本地 agent runtime dashboard，首要风险在 runtime 状态、审批和恢复，而不是 SSR。

### Web API 设计

新增 `packages/api`，用 FastAPI 暴露以下接口：

```text
POST   /api/chat/runs
GET    /api/chat/runs/{run_id}
POST   /api/chat/runs/{run_id}/resume

POST   /api/plans
GET    /api/plans/{plan_id}

POST   /api/plan-runs
GET    /api/plan-runs
GET    /api/plan-runs/{plan_run_id}
POST   /api/plan-runs/{plan_run_id}/approve
POST   /api/plan-runs/{plan_run_id}/reject
POST   /api/plan-runs/{plan_run_id}/recover

GET    /api/sessions/{session_id}/messages
GET    /api/users/{user_id}/memories
GET    /api/audit/events

GET    /api/events/runs/{run_id}
GET    /api/events/plan-runs/{plan_run_id}
```

REST 返回稳定 JSON；`/api/events/*` 返回 SSE：

```json
{
  "id": "event-offset-or-ts",
  "event": "tool_started",
  "data": {
    "run_id": "...",
    "step": 1,
    "payload": {}
  }
}
```

事件流建议直接复用 `AgentEvent`，再补一层对外 envelope：

```python
class RuntimeEventEnvelope(BaseModel):
    id: str
    scope: Literal["run", "plan_run"]
    event_type: str
    run_id: str | None = None
    plan_run_id: str | None = None
    step_id: str | None = None
    ts: float
    payload: dict[str, Any]
```

### 实时通信选择

第一阶段推荐 SSE，而不是 WebSocket：

- 运行事件、日志、状态变化都是服务端到客户端的单向流。
- 审批、取消、恢复可以走普通 REST mutation。
- 浏览器原生支持 `EventSource`，断线重连和 `Last-Event-ID` 模式更贴合 run timeline。
- WebSocket 适合强交互协同编辑、双向终端、多人 presence，当前不是第一优先级。

如果后续要做“浏览器内终端”、多人协作或持续双向 agent control，再补 WebSocket。

### 后端运行管理

不能把长任务简单放在请求协程里执行。建议新增 in-process `RunManager`：

```text
RunManager
  start_chat_run(request) -> run_id
  start_plan_run(request) -> plan_run_id
  approve(plan_run_id, decision)
  recover(plan_run_id)
  cancel(run_id | plan_run_id)
  subscribe(scope_id) -> async iterator[event]
```

第一阶段可以用 `asyncio.create_task()` + 文件 store。关键要求：

- 每个运行必须立即落盘，返回 `run_id` 或 `plan_run_id`。
- 所有状态变化写 checkpoint / plan_run_store。
- `EventBus` 增加 subscriber，把事件写入内存 ring buffer + audit log。
- SSE 订阅先 replay ring buffer，再推送新事件。
- 进程重启后，`recover` 从文件 store 恢复未完成 plan run。

第二阶段再引入 SQLite，把 run、event、artifact、approval_request 结构化存储。

### 前端状态模型

TanStack Query query key 建议：

```text
["planRuns", filters]
["planRun", planRunId]
["run", runId]
["sessionMessages", sessionId]
["memories", userId]
["auditEvents", filters]
```

mutation：

```text
createChatRun
createPlan
createPlanRun
approvePlanRun
rejectPlanRun
recoverPlanRun
cancelRun
```

SSE 收到事件后：

- append 到当前 detail timeline。
- 对 `run` / `planRun` query 做局部 patch。
- 在 `run_completed`、`run_failed`、`waiting_human` 时 invalidate list query。

### UI 关键组件

- `RunStatusBadge`：`idle/running/tool_running/waiting_human/completed/failed/cancelled`。
- `PlanRunTable`：目标、状态、步骤数、当前等待步骤、开始/结束时间。
- `StepTimeline`：每个 step 的状态、duration、run_id、output/error。
- `ToolCallCard`：tool name、arguments、approval、stdout/stderr、changed_files。
- `ApprovalDialog`：显示 pending request，支持 edit JSON、approve、reject。
- `EventStreamPanel`：按时间展示 model/tool/human/checkpoint 事件。
- `MemoryPanel`：按 user_id 显示 recent memory 和 tags。

Web 界面要突出“可控、可恢复、可审计”，不要只做聊天框。这个项目和普通 chat UI 的核心差异是 plan run、审批、checkpoint 和 event timeline。

## CLI 端实现方案

### CLI 设计目标

CLI 不是 Web 的简化版，而是本地高效率入口：

- 快速启动 chat / plan / orchestrate。
- 直接审批 waiting run。
- 以 live view 观察长任务。
- 能在无 Web 服务时直接 import 本地 runtime。
- 能在远端模式下调用 API server。

### 推荐命令结构

用 Typer 重写为：

```text
agent chat "message"
agent chat --session-id demo
agent plan "goal"
agent run "goal"
agent runs list
agent runs show <run_id>
agent runs watch <run_id>
agent plan-runs list
agent plan-runs show <plan_run_id>
agent approve <plan_run_id>
agent reject <plan_run_id>
agent recover <plan_run_id>
agent sessions show <session_id>
agent memory show --user-id demo-user
agent serve api
agent serve web
```

全局选项：

```text
--provider mock|openai_compatible
--user-id demo-user
--session-id demo-session
--api-url http://127.0.0.1:8000
--mode local|remote
--json
--verbose
```

### Local 与 Remote 双模式

`local` 模式：

- CLI 直接调用 `packages/app_service`。
- 适合当前开发和本机使用。
- 不需要启动 API server。

`remote` 模式：

- CLI 用 HTTP client 调用 FastAPI。
- `watch` 命令订阅 SSE。
- 适合 Web server 常驻运行或跨机器访问。

建议默认 `local`，因为当前项目是个人生产环境 runtime；当用户显式设置 `AGENT_API_URL` 或 `--mode remote` 时走远端。

### CLI 展示

Rich 用法建议：

- `runs list` 用 `Table` 展示 ID、status、goal/task、updated_at。
- `plan-runs show` 用树状结构展示 step_runs。
- `watch` 用 `Live` 动态刷新 timeline。
- 长任务用 `Progress` 展示 plan steps 完成度。
- `--json` 输出机器可读 JSON，供脚本集成。

审批命令应支持三种交互：

```powershell
agent approve <plan_run_id>
agent approve <plan_run_id> --edit args.json
agent reject <plan_run_id> --reason "not safe"
```

如果不带 `--edit`，交互式打印 pending request，再确认是否 approve/reject。

### CLI 代码结构

```text
packages/cli/
  app.py           Typer root app
  commands/
    chat.py
    plan.py
    runs.py
    approvals.py
    memory.py
    serve.py
  render.py        Rich table/live/progress
  client.py        remote HTTP/SSE client
  options.py       common options
```

`main.py` 保留兼容入口：

```python
from packages.cli.app import app

if __name__ == "__main__":
    app()
```

如果担心一次性迁移风险，可以先新增 `agent_cli.py` 或 `packages/cli/app.py`，待命令稳定后再替换 `main.py`。

## 共享服务层

最重要的改造是先抽出 `packages/app_service`。目标是把 `main.py` 里的装配逻辑沉到可测试服务中：

```text
ChatService
  run_chat(user_id, session_id, message, provider) -> AgentState
  resume_run(run_id, decision) -> AgentState

PlanService
  create_plan(goal, context, provider) -> TaskPlan

PlanRunService
  start(goal, context, user_id, provider) -> PlanRun
  approve(plan_run_id, decision) -> PlanRun
  recover(plan_run_id) -> PlanRun

QueryService
  list_plan_runs(filters)
  get_plan_run(id)
  get_run(id)
  get_session_messages(id)
  list_memories(user_id)
```

服务层收益：

- CLI、Web API、测试共用同一行为。
- provider 配置、store、runtime bootstrap 不再散落在入口层。
- 后续更换文件存储或增加队列，不影响 UI。
- approval/recover 的边界更清晰。

## 数据与持久化演进

第一阶段：

- 保留 `runtime_data/*.json`。
- 给 store 增加 list/filter 方法，例如 list recent runs、list waiting plan runs。
- event stream 用内存 ring buffer + audit log。

第二阶段：

- 引入 SQLite。
- 表：`sessions`、`messages`、`runs`、`checkpoints`、`plans`、`plan_runs`、`step_runs`、`events`、`approval_requests`、`memories`。
- 文件 workspace 和 artifact 仍保留在文件系统，数据库只存路径和 metadata。

第三阶段：

- 支持 Postgres、用户认证、多设备访问。
- 长任务从 in-process manager 迁移到 queue worker。

## 安全与权限

Web/CLI 一定要继承当前 guardrail 和 approval 策略：

- API 不直接暴露任意文件读写，只暴露 runtime 支持的工具调用结果和 artifact metadata。
- approve/edit arguments 必须再次走 `validate_tool_args`。
- Web UI 编辑 JSON 后，后端仍以服务端校验为准。
- audit log 要脱敏 API key、环境变量、完整 shell 输出中的敏感内容。
- 本地 Web server 默认只监听 `127.0.0.1`。
- 如需远端访问，再增加 token auth。

## 分阶段落地计划

### Phase 1：服务层与 CLI 稳定化

- 新增 `packages/app_service`。
- 把 `main.py` 中 chat/plan/orchestrate/approve/resume 的重复装配下沉。
- 新增 Typer CLI，保留旧命令兼容。
- Rich 展示 `plan-runs show`、`runs list`、`watch`。
- 给 store 增加 list 方法和测试。

验收：

- 现有 `main.py` 命令行为不回退。
- 新 CLI 能完成 chat、plan、run、approve、recover。
- `--json` 输出可被脚本消费。

### Phase 2：FastAPI API 与 SSE

- 新增 `packages/api`。
- 暴露 chat run、plan、plan run、approval、session、memory 查询接口。
- `EventBus` 增加 stream subscriber。
- 实现 `/api/events/runs/{run_id}` 和 `/api/events/plan-runs/{plan_run_id}`。
- CLI remote 模式接入 API。

验收：

- Web/CLI 可同时观察同一个 running plan run。
- waiting approval 可通过 API approve/reject/edit。
- 进程重启后可 recover 未完成 plan run。

### Phase 3：Web 工作台

- 新增 `web/`，使用 Vite React + TypeScript + TanStack Query。
- 实现 run list、run detail、plan run detail、approval dialog、memory view。
- SSE 驱动 timeline 实时刷新。
- 增加基本错误态、空态、loading 态。

验收：

- 用户能从浏览器发起任务、观察工具调用、处理审批、查看最终结果。
- 失败 run 有可诊断的 event timeline 和 error。
- 不需要打开终端也能完成主要 workflow。

### Phase 4：存储与产品化

- SQLite event/run store。
- artifact 管理。
- 认证和本地 token。
- 更细的权限策略和审计脱敏。
- 可选 WebSocket：浏览器终端、多人协作、实时控制。

## 风险点

- 长任务生命周期：浏览器请求断开不应取消 agent run。
- 并行 step 状态：同一 plan run 内多个 step 可能同时产生日志和审批，事件必须带 `step_id`。
- 审批一致性：一个 pending approval 只能被处理一次，approve/reject 需要幂等。
- 文件存储并发：多个 run 同时写 JSON 时可能有竞争，后续需要文件锁或 SQLite。
- 输出体积：runtime_messages、tool stdout、audit log 可能很大，API 要分页、截断和按需加载。
- 安全边界：Web server 一旦开放网络访问，文件工具和 bash 工具的审批/审计必须更严格。

## 推荐立即开始的任务

1. 先写 `packages/app_service`，把当前 `main.py` 变薄。
2. 用 Typer + Rich 做新 CLI，验证服务层设计。
3. 给 store 增加 list/filter，满足 CLI/Web 查询。
4. 接 FastAPI REST，不急着做完整 Web。
5. 接 SSE 和 run manager，再做 React 工作台。

这个顺序能先稳住后端边界，再上界面。否则 Web 会被迫直接读 `runtime_data` 或重复拼装 runtime，后续维护成本会快速升高。
