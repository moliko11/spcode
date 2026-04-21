# Personal Code Agent — 开发与优化路线图

> **定位**：全能型 Code Agent，覆盖代码编辑/重构/调试/审查 + 需求分析/任务拆解/进度追踪  
> **首要能力**：① 工作流规划（任务拆分 + 并行执行）② 长期记忆（跨文件/跨会话的项目上下文）  
> **目标用户**：个人优先，保留通用化/开源扩展能力

---

## 一、当前进度总览

### 已完成的能力矩阵

| 层级 | 组件 | 状态 | 所在位置 |
|------|------|------|----------|
| **LLM 适配** | FallbackChatModel / 多后端回退 | ✅ 完成 | `packages/model_loader.py` |
| **基础 I/O** | FileRead / FileWrite / FileEdit | ✅ 完成 | `packages/core_io/file_tools.py` |
| **基础 I/O** | Glob / Grep (含 ripgrep 回退) | ✅ 完成 | `packages/core_io/search_tools.py` |
| **基础 I/O** | BashSessionManager (持久会话) | ✅ 完成 | `packages/core_io/bash_tools.py` |
| **基础 I/O** | WebSearch (Tavily→SerpAPI→DuckDuckGo) / WebFetch | ✅ 完成 | `packages/core_io/web_tools.py` |
| **基础 I/O** | 路径安全 / 二进制检测 / 忽略规则 | ✅ 完成 | `packages/core_io/pathing.py` |
| **工具包装** | 12 个高级 Tool 包装 (含 MCP / Skill / ToolSearch) | ✅ 完成 | `packages/tools/*` |
| **运行时** | Agent Loop (决策→工具待执行→已执行→人工等待) | ✅ 完成 | `examples/example6.py` |
| **运行时** | 事件总线 + 日志/审计订阅 | ✅ 完成 | `examples/example6.py` |
| **运行时** | 检查点保存/恢复/断点续跑 | ✅ 完成 | `examples/example6.py` |
| **运行时** | 权限控制 + 审批策略 (never/always/on_write/on_high_risk) | ✅ 完成 | `examples/example6.py` |
| **运行时** | 守卫引擎 (输入/参数/输出/最终结果校验) | ✅ 完成 | `examples/example6.py` |
| **运行时** | 预算控制 (步数/调用数/时间) | ✅ 完成 | `examples/example6.py` |
| **运行时** | 重试策略 + 幂等存储 | ✅ 完成 | `examples/example6.py` |
| **运行时** | 动态工具加载 (tool_search → 运行时注入) | ✅ 完成 | `examples/example6.py` |
| **运行时** | 工具目录 (TOOL_CATALOG) + 按需发现 | ✅ 完成 | `examples/example6.py` |
| **入口** | CLI (chat / orchestrate / show-session / show-memory) | ⚠️ 骨架完成 | `main.py` |

### 当前架构核心问题

**所有运行时逻辑（~2100 行）堆在 `example6.py` 一个文件里，尚未拆分为独立模块。**

`main.py` 已经引用 `packages.runtime.bootstrap`，但该模块尚不存在。

---

## 二、缺失能力分析（按你的优先级排序）

| 能力 | 现状 | 你的优先级 | 阶段 |
|------|------|-----------|------|
| **模块化拆分** | 2100行堆在 example6.py，main.py 引用的模块不存在 | 🔴 地基 | Phase 1 |
| **工作流规划** | 不存在；当前只能线性循环，无法拆分/并行子任务 | 🔴 最高 | Phase 2 |
| **长期记忆** | 只有短期会话记录，无跨文件/跨会话项目上下文 | 🔴 最高 | Phase 2 |
| **上下文管理** | 仅滑动窗口8轮，无摘要/压缩/项目知识注入 | 🟡 P1 | Phase 2 |
| **代码任务提示词** | 通用型提示词，缺乏 Code Agent 专用策略 | 🟡 P1 | Phase 3 |
| **并行工具调用** | 每步只执行一个工具，独立任务无法并行 | 🟡 P1 | Phase 3 |
| **自我反思/错误恢复** | 工具失败后无智能分析和策略调整 | 🟡 P1 | Phase 3 |
| **多轮编排** | main.py 有入口但实现缺失 | 🔴 P0 | Phase 2 |
| **代码理解增强** | 无 AST/调用图/符号分析 | 🟠 P2 | Phase 4 |
| **多 Agent 协作** | 不存在 | 🟠 P2 | Phase 5 |
| **前端/交互界面** | 不存在 | 🟠 P2 |

---

## 三、分阶段开发路线

**执行顺序总览：**

```
Phase 1  模块化拆分       ← 地基，必须先完成
    ↓
Phase 2  长期记忆 + 工作流规划  ← 你的两个最高优先级
    ↓
Phase 3  Code Agent 专项能力   ← 针对代码场景的深化
    ↓
Phase 4  增强 Loop + 提示词体系  ← 更聪明的循环方式
    ↓
Phase 5  高级能力              ← 多 Agent、代码理解、UI
```

---

### Phase 1：模块化拆分（地基）

> **目标**：把 `example6.py` 的 2100 行拆成独立模块，让 `main.py` 能实际运行起来。  
> **完成标志**：`python main.py chat "hello"` 可以正常调用真实 LLM 返回结果。

#### 目标目录结构

```
packages/
├── model_loader.py              # ✅ 已有
├── core_io/                     # ✅ 已有
├── tools/                       # ✅ 已有
└── runtime/                     # 🆕 本阶段创建
    ├── __init__.py
    ├── config.py          # 所有常量、路径、工具目录
    ├── models.py          # 数据类、枚举、异常
    ├── events.py          # 事件总线、订阅者
    ├── store.py           # SessionStore、CheckpointStore
    ├── guardrail.py       # GuardrailEngine
    ├── permission.py      # PermissionController、ApprovalController
    ├── budget.py          # BudgetController、RetryPolicy、IdempotencyStore
    ├── registry.py        # ToolRegistry
    ├── executor.py        # ToolExecutor
    ├── llm_client.py      # NativeToolCallingLLMClient
    ├── message_builder.py # MessageBuilder + 系统提示词
    ├── agent_loop.py      # AgentRuntime（核心循环）
    └── bootstrap.py       # build_runtime() 组装入口
```

#### 任务清单

| # | 任务 |
|---|------|
| 1.1 | `runtime/config.py` — 所有路径/限制常量/工具目录 |
| 1.2 | `runtime/models.py` — 所有数据类、枚举、异常、工具函数 |
| 1.3 | `runtime/events.py` — EventBus、订阅者 |
| 1.4 | `runtime/store.py` — FileSessionStore、FileCheckpointStore |
| 1.5 | `runtime/guardrail.py` — GuardrailEngine |
| 1.6 | `runtime/permission.py` — PermissionController、ApprovalController |
| 1.7 | `runtime/budget.py` — BudgetController、RetryPolicy、IdempotencyStore |
| 1.8 | `runtime/registry.py` — ToolRegistry |
| 1.9 | `runtime/executor.py` — ToolExecutor |
| 1.10 | `runtime/llm_client.py` — NativeToolCallingLLMClient |
| 1.11 | `runtime/message_builder.py` — MessageBuilder |
| 1.12 | `runtime/agent_loop.py` — AgentRuntime |
| 1.13 | `runtime/bootstrap.py` — build_runtime() |
| 1.14 | 验证 `main.py chat` 可运行 |
| 1.15 | 迁移并修复现有测试的 import 路径 |

---

### Phase 2：长期记忆 + 工作流规划（核心双引擎）

> **目标**：实现你最高优先级的两个能力。  
> 这两个能力强耦合——记忆为规划提供项目上下文，规划的执行结果又写回记忆。  
> 建议同步开发，先各自建模型，再做集成。

---

#### 2A：长期记忆系统

**跨文件/跨会话的项目上下文，让 Agent 知道你的代码库是什么样的。**

##### 三类记忆

| 类型 | 内容 | 示例 |
|------|------|------|
| `episode`（事件记忆） | 某次任务做了什么 | "2026-04-17 重构了 runtime/模块，拆成13个文件" |
| `semantic`（语义记忆） | 项目知识/用户偏好 | "该项目用 asyncio，用 Qwen3 本地模型，工作目录是 runtime_data" |
| `procedural`（过程记忆） | 成功的操作流程 | "修改 ToolSpec 需要同步更新 guardrail.py 的校验逻辑" |

##### 模块设计

```
packages/memory/
├── __init__.py
├── models.py          # MemoryEntry, MemoryType, MemorySearchResult
├── store.py           # FileMemoryStore (v1 JSON), VectorMemoryStore (v2 接口)
├── manager.py         # MemoryManager — 核心管理器
├── extractor.py       # 从 AgentState 中自动提取记忆
└── context_injector.py  # 将相关记忆注入到 MessageBuilder
```

##### 关键接口

```python
class MemoryManager:
    async def remember_run(self, state: AgentState) -> None:
        """运行结束后，用 LLM 提取本次任务的关键信息存入记忆"""

    async def recall(self, query: str, user_id: str, limit: int = 5) -> list[MemoryEntry]:
        """检索与当前任务最相关的记忆（v1: 关键词匹配，v2: 向量检索）"""

    async def update_project_knowledge(self, key: str, value: str, user_id: str) -> None:
        """更新/覆盖某条语义记忆（如项目结构变化时主动更新）"""

class ContextInjector:
    async def inject(self, state: AgentState, messages: list[Message]) -> list[Message]:
        """在系统提示词后插入相关记忆，形如：
        [System] ...
        [System/Memory] 相关项目记忆：
          - 该项目的运行时入口是 packages/runtime/bootstrap.py
          - 你上次修改了 ToolSpec，注意同步 executor.py
        [Human] 用户当前问题
        """
```

##### 记忆写入时机

```
任务完成 → MemoryManager.remember_run()
    ├── 提炼本次任务摘要 (episode)
    ├── 提炼项目知识更新 (semantic)：涉及的文件/接口/约定
    └── 提炼操作模式 (procedural)：成功的工具调用序列
```

##### 任务清单

| # | 任务 |
|---|------|
| 2A.1 | `memory/models.py` — MemoryEntry, MemoryType |
| 2A.2 | `memory/store.py` — FileMemoryStore (按 user_id 分文件存储) |
| 2A.3 | `memory/extractor.py` — 从 AgentState 提取记忆的规则/LLM调用 |
| 2A.4 | `memory/manager.py` — MemoryManager (remember / recall / forget) |
| 2A.5 | `memory/context_injector.py` — 将记忆注入 messages |
| 2A.6 | `runtime/message_builder.py` 集成 ContextInjector |
| 2A.7 | `runtime/agent_loop.py` 集成 `remember_run` (在 `_finalize` 中调用) |
| 2A.8 | `main.py show-memory` 命令实现 |
| 2A.9 | 测试：记忆存储、检索、注入 |

---

#### 2B：工作流规划系统

**把一个复杂目标拆成有序的子任务，并行执行独立任务，知道哪里完成了/哪里失败了。**

> 展开设计：见 [`WORKFLOW_PLANNING_SYSTEM.md`](./WORKFLOW_PLANNING_SYSTEM.md)。该文档在本节 MVP 设计基础上补充了 Claude Code / Codex 风格的 plan-only 模式、计划审批、工作流状态机、step 验收标准、evidence/attempt 记录、资源锁、hooks、ProjectGuideLoader、ToolSearch 集成、持久化恢复、测试策略和分阶段落地路线。

##### Plan-Execute-Verify 循环

```
用户输入目标
    ↓
Planner.create_plan()   ← LLM 分析目标，输出有依赖关系的任务图
    ↓
Scheduler.next_ready()  ← 找出当前可以执行的任务（依赖已满足）
    ↓
Executor.run_step()     ← AgentRuntime 执行单个子任务
    ↓
Verifier.check()        ← 检查子任务完成质量（可选）
    ↓
Planner.replan()?       ← 如果失败或偏差，局部重规划
    ↓
所有任务完成 → Orchestrator 汇总最终输出
```

##### 模块设计

```
packages/planner/
├── __init__.py
├── models.py      # TaskPlan, TaskStep, PlanStatus, StepStatus, Dependency
├── planner.py     # Planner — LLM 生成/修订计划
├── scheduler.py   # Scheduler — 拓扑排序，找出可并行执行的就绪任务
├── verifier.py    # Verifier — 验证子任务结果
└── prompts.py     # 规划用的提示词模版

packages/orchestrator/
├── __init__.py
├── models.py          # OrchestrationResult, TurnRecord
└── orchestrator.py    # Orchestrator — 驱动整个 Plan→Execute 循环
```

##### 关键数据结构

```python
@dataclass
class TaskStep:
    step_id: str
    title: str                       # "重构 runtime/models.py 的数据类"
    description: str                 # 详细说明
    step_type: Literal["code", "analysis", "research", "review", "test"]
    expected_tools: list[str]        # ["file_read", "file_edit", "grep"]
    dependencies: list[str]          # 前置 step_id 列表
    acceptance_criteria: str | None  # 验收标准（用于 Verifier）
    status: StepStatus
    result_summary: str | None
    artifacts: list[str]             # 生成的文件路径

@dataclass
class TaskPlan:
    plan_id: str
    goal: str
    context: dict          # 规划时的上下文（项目结构/相关记忆）
    steps: list[TaskStep]
    status: PlanStatus
    replan_count: int      # 重规划次数
    created_at: float
```

##### 关键接口

```python
class Planner:
    async def create_plan(self, goal: str, project_context: str) -> TaskPlan:
        """
        提示词要求 LLM 输出结构化计划，包含：
        - 子任务列表（各自的类型/工具/依赖）
        - 依赖关系（DAG，非线性）
        - 验收标准
        JSON 格式输出，解析后转为 TaskPlan
        """
    
    async def replan(self, plan: TaskPlan, failed_step: TaskStep, error: str) -> TaskPlan:
        """局部重规划：只修改失败步骤及其下游"""

class Scheduler:
    def get_ready_steps(self, plan: TaskPlan) -> list[TaskStep]:
        """拓扑排序，返回所有依赖已完成的待执行步骤（可并行）"""

class Orchestrator:
    async def orchestrate(self, goal: str, user_id: str, session_id: str) -> OrchestrationResult:
        plan = await self.planner.create_plan(goal, context)
        while not plan.is_done():
            ready = self.scheduler.get_ready_steps(plan)
            # 并行执行所有就绪步骤
            results = await asyncio.gather(*[self._run_step(s) for s in ready])
            for step, result in zip(ready, results):
                if not result.ok and await self.planner.should_replan(plan, step):
                    plan = await self.planner.replan(plan, step, result.error)
        return OrchestrationResult(plan=plan, ...)
```

##### 任务清单

| # | 任务 |
|---|------|
| 2B.1 | `planner/models.py` — TaskPlan, TaskStep, 枚举 |
| 2B.2 | `planner/prompts.py` — 任务拆分提示词（结构化 JSON 输出格式） |
| 2B.3 | `planner/planner.py` — Planner (create_plan + replan) |
| 2B.4 | `planner/scheduler.py` — Scheduler (拓扑排序，支持并行就绪队列) |
| 2B.5 | `planner/verifier.py` — Verifier (基于验收标准判断完成质量) |
| 2B.6 | `tools/TodoWriteTool` — 轻量计划/待办状态维护 |
| 2B.7 | `tools/EnterPlanModeTool` / `tools/ExitPlanModeTool` — 计划模式进入/退出与审批边界 |
| 2B.8 | `tools/TaskCreateTool` / `TaskUpdateTool` / `TaskListTool` — workflow task 落盘、更新和查询 |
| 2B.9 | `tools/TaskOutputTool` / `TaskStopTool` — 任务结果导出和中止 |
| 2B.10 | `WebSearchTool` / `WebFetchTool` 适配 workflow evidence 记录 |
| 2B.11 | `orchestrator/models.py` — OrchestrationResult, TurnRecord |
| 2B.12 | `orchestrator/orchestrator.py` — Orchestrator (驱动循环) |
| 2B.13 | `runtime/agent_loop.py` 新增 `orchestrate()` 方法，调用 Orchestrator |
| 2B.14 | `main.py orchestrate` 命令实现 |
| 2B.15 | 测试：计划生成、拓扑调度、重规划、任务工具状态流转 |

> Agent/Team 工具（`AgentTool`, `SendMessageTool`, `TeamCreateTool`, `TeamDeleteTool`）后置到工作流和任务模型稳定之后；`NotebookEditTool` 等明确支持 `.ipynb` 编辑策略后再接入。

##### 2B 实施节奏（缩小动作跨度）

为避免一次性改动过大，2B 按 W1 -> W3 先落地，W4/W5 延后：

| 批次 | 范围 | 时间盒 | 完成标志 |
|---|---|---|---|
| W1（仅计划） | `planner/models.py`、`planner/prompts.py`、`planner/planner.py`、`planner/store.py`、`main.py plan` | 1-2 天 | 能产出并保存带 dependencies + acceptance criteria 的 plan（不执行工具） |
| W2（顺序执行） | `orchestrator/models.py`、`orchestrator/orchestrator.py`、`orchestrator/executor.py`、`main.py orchestrate` 最小接线 | 1-2 天 | 按 step 串行执行并记录 step 状态 |
| W3（验证与重规划） | `planner/verifier.py`、`planner/replanner.py`、max_attempts、失败重规划事件 | 1-2 天 | step 失败后能给出 retry/replan，而不是直接失败 |

当前阶段先不做：

- 并行调度与资源锁（W4）
- 持久化恢复工作流（W5）
- Agent/Team 多代理工具

落地顺序建议：

1. 先打通 Plan-only（可审阅、可保存、可回放）。
2. 再做串行执行，保证可观测和可回滚。
3. 最后加 verifier/replanner，提升稳定性。

> 详细分期和验收命令见 [`WORKFLOW_PLANNING_SYSTEM.md`](./WORKFLOW_PLANNING_SYSTEM.md) 的“21. 分阶段落地任务”。

---

#### 2C：记忆 × 规划 集成

| # | 任务 |
|---|------|
| 2C.1 | 规划时注入相关记忆作为 `project_context` |
| 2C.2 | 每个 step 完成后更新过程记忆（完成了什么/用了哪些工具） |
| 2C.3 | Orchestration 结束后调用 `remember_run()` 存储完整任务记忆 |
| 2C.4 | 集成测试：完整 orchestrate → 记忆存储 → 下次调用时注入的端到端流程 |

---

### Phase 3：Code Agent 专项能力

> **目标**：针对代码编辑、重构、调试、审查场景的专项优化。

#### 3.1 代码任务专用工具

```python
# 新增工具，注册到 ToolRegistry

class RunTestsTool:
    """运行项目测试，返回失败列表"""

class LintTool:
    """运行 ruff/pylint，返回代码问题"""

class GitDiffTool:
    """获取 git diff，理解最近的修改"""

class GitLogTool:
    """查看 commit 历史，理解演进脉络"""

class FindSymbolTool:
    """基于 AST 查找类/函数/变量定义位置"""
```

#### 3.2 代码审查流程

```
用户：帮我 review 这个 PR / 这段代码
    ↓
Planner 拆分为子任务：
    1. 读取目标文件（FileReadTool）
    2. 分析代码结构（FindSymbolTool / GrepTool）
    3. 运行测试（RunTestsTool）
    4. 运行 Lint（LintTool）
    5. 生成 review 报告（final output）
```

#### 3.3 进度追踪工具

```python
# packages/tracking/

class TaskTracker:
    async def create_task(self, title: str, description: str) -> Task: ...
    async def update_status(self, task_id: str, status: str, note: str) -> None: ...
    async def list_tasks(self, user_id: str, status_filter: str | None) -> list[Task]: ...

class ProgressReporter:
    async def generate_report(self, user_id: str) -> str:
        """生成人类可读的进度报告"""
```

#### 任务清单

| # | 任务 |
|---|------|
| 3.1 | `tools/RunTestsTool/` — 运行测试工具 |
| 3.2 | `tools/LintTool/` — 代码 Lint 工具 |
| 3.3 | `tools/GitDiffTool/` — Git diff 工具 |
| 3.4 | `tools/FindSymbolTool/` — AST 符号查找 |
| 3.5 | `tracking/` — 任务/进度追踪模块 |
| 3.6 | `planner/prompts.py` 新增 Code 专用规划模版 |
| 3.7 | 代码审查端到端测试 |

---

### Phase 4：增强 Loop + 提示词体系

> **目标**：让单次循环更聪明，减少无效步骤，提升完成质量。

#### 4.1 并行工具调用

```python
# agent_loop.py — 当 LLM 返回多个 tool_calls 时，并行执行无依赖的调用

async def _execute_tool_calls_batch(self, state, tool_calls):
    # 检查 tool_calls 中是否有写冲突（同文件写操作）
    parallel_safe = self._split_parallel_safe(tool_calls)
    for group in parallel_safe:
        results = await asyncio.gather(*[
            self.tool_executor.execute(state, call) for call in group
        ])
        # 将所有结果追加到 runtime_messages
```

#### 4.2 自我反思

```python
# runtime/reflection.py

class ReflectionEngine:
    async def reflect_on_failure(self, state, error) -> str:
        """工具失败后分析：是参数错了？路径错了？逻辑错了？建议下一步。"""

    async def reflect_on_loop(self, state) -> str:
        """检测到重复行为后，建议破局策略"""

    async def pre_finalize_check(self, state) -> bool:
        """输出前自检：是否真正回答了问题？是否遗漏了子任务？"""
```

#### 4.3 提示词体系

```
packages/runtime/prompts/
├── base.py             # 基础人设 + 核心行为规则
├── code_agent.py       # Code Agent 专用（工具优先级、代码规范）
├── planner_mode.py     # 规划模式（目标分解、依赖分析）
├── reviewer_mode.py    # 审查模式（发现问题、建议改进）
├── react.py            # ReAct 格式（Thought→Action→Observation）
└── selector.py         # 根据任务类型自动选择提示词策略
```

#### 4.4 退化处理

```python
class DegradedHandler:
    async def handle(self, state: AgentState) -> AgentState:
        """预算耗尽时：收集已完成的部分结果，生成"尽力"回答，标记 DEGRADED"""
```

#### 任务清单

| # | 任务 |
|---|------|
| 4.1 | 并行 tool_calls batch 执行（写冲突检测） |
| 4.2 | `runtime/reflection.py` — ReflectionEngine |
| 4.3 | 集成 reflect_on_failure 到 agent_loop |
| 4.4 | `runtime/prompts/` — 提示词体系 |
| 4.5 | `runtime/prompts/selector.py` — 策略选择器 |
| 4.6 | `DegradedHandler` 实现 |
| 4.7 | 测试：并行调用、反思触发、退化分支 |

---

### Phase 5：高级能力（开源友好）

> **目标**：向外扩展，为将来的通用化和开源做准备。

| 能力 | 内容 |
|------|------|
| **多 Agent** | Coordinator + 专职 Agent（Coder / Reviewer / Tester） |
| **向量记忆** | 用 Chroma/Qdrant 替换 FileMemoryStore，支持语义检索 |
| **Web UI** | FastAPI + WebSocket，实时展示事件流、审批界面、记忆面板 |
| **插件化工具** | 通过 YAML 描述文件注册新工具，无需改代码 |
| **代码库索引** | 启动时扫描工作区，建立文件/符号/依赖的结构化索引 |
| **通用配置文件** | `agent.config.yaml`，方便其他人开箱即用 |

---

## 四、各阶段交付物

| Phase | 完成标志 |
|-------|---------|
| Phase 1 | `python main.py chat "列出 runtime 目录结构"` 正常运行 |
| Phase 2A | `python main.py show-memory` 显示上次任务的记忆摘要 |
| Phase 2B | `python main.py orchestrate "重构 example6.py 为独立模块"` 自动规划并执行 |
| Phase 3 | `python main.py orchestrate "review packages/runtime/executor.py"` 自动输出完整审查报告 |
| Phase 4 | 同等任务下，工具调用步数减少 ≥20%（并行+反思减少无效调用） |
| Phase 5 | 其他人能 fork 并修改 `agent.config.yaml` 跑起来 |

---

## 五、关键设计原则

1. **每 Phase 可运行**：不做半成品，每个阶段结束项目都是可用的
2. **记忆驱动规划**：规划时注入项目记忆，而不是每次从零开始理解代码库
3. **安全优先**：守卫/权限/路径限制贯穿始终，不因新功能而松懈
4. **可观测**：事件总线 + 审计日志让每步可追溯，方便调试 Agent 行为
5. **向后兼容**：检查点/记忆文件格式变更时提供迁移脚本
6. **个人习惯优先**：配置文件优先读取本地，通用化是第二层而不是第一层


#### 目标目录结构

```
packages/
├── __init__.py
├── model_loader.py              # ✅ 已有
├── core_io/                     # ✅ 已有
│   ├── bash_tools.py
│   ├── file_tools.py
│   ├── search_tools.py
│   ├── web_tools.py
│   ├── models.py
│   └── pathing.py
├── tools/                       # ✅ 已有
│   ├── BashTool/
│   ├── FileEditTool/
│   ├── ...
│   └── ToolSearchTool/
├── runtime/                     # 🆕 新建
│   ├── __init__.py
│   ├── models.py                # 数据模型: RunStatus, Phase, ToolSpec, AgentState, ToolCall, ToolResult 等
│   ├── events.py                # 事件系统: EventType, EventBus, AgentEvent, 订阅者
│   ├── store.py                 # 存储层: FileSessionStore, FileCheckpointStore
│   ├── guardrail.py             # 安全引擎: GuardrailEngine
│   ├── permission.py            # 权限+审批: PermissionController, ApprovalController
│   ├── budget.py                # 预算+幂等+重试: BudgetController, RetryPolicy, IdempotencyStore
│   ├── registry.py              # 工具注册: ToolRegistry
│   ├── executor.py              # 工具执行: ToolExecutor
│   ├── llm_client.py            # LLM客户端: NativeToolCallingLLMClient
│   ├── message_builder.py       # 消息构建: MessageBuilder, 系统提示词
│   ├── agent_loop.py            # 核心循环: AgentRuntime._continue
│   ├── bootstrap.py             # 运行时组装: build_runtime()
│   └── config.py                # 配置常量
├── memory/                      # 🆕 Phase 2
├── planner/                     # 🆕 Phase 3
└── orchestrator/                # 🆕 Phase 3
```

#### 拆分任务清单

| # | 任务 | 来源 |
|---|------|------|
| 1.1 | 创建 `runtime/config.py` — 提取所有常量 (MAX_STEPS, 路径等) | example6 头部 |
| 1.2 | 创建 `runtime/models.py` — 提取所有数据类和异常 | example6 数据类 |
| 1.3 | 创建 `runtime/events.py` — 提取事件系统 | example6 EventBus 相关 |
| 1.4 | 创建 `runtime/store.py` — 提取存储层 | example6 SessionStore/CheckpointStore |
| 1.5 | 创建 `runtime/guardrail.py` — 提取守卫引擎 | example6 GuardrailEngine |
| 1.6 | 创建 `runtime/permission.py` — 提取权限+审批 | example6 Permission/Approval |
| 1.7 | 创建 `runtime/budget.py` — 提取预算/重试/幂等 | example6 BudgetController 等 |
| 1.8 | 创建 `runtime/registry.py` — 提取工具注册 | example6 ToolRegistry |
| 1.9 | 创建 `runtime/executor.py` — 提取工具执行器 | example6 ToolExecutor |
| 1.10 | 创建 `runtime/llm_client.py` — 提取 LLM 客户端 | example6 NativeToolCallingLLMClient |
| 1.11 | 创建 `runtime/message_builder.py` — 提取消息构建 | example6 MessageBuilder |
| 1.12 | 创建 `runtime/agent_loop.py` — 提取 AgentRuntime | example6 AgentRuntime |
| 1.13 | 创建 `runtime/bootstrap.py` — 提取 build_runtime() | example6 build_runtime |
| 1.14 | 验证 main.py 能正常运行 | — |
| 1.15 | 迁移并适配现有测试 | tests/ |

---

### Phase 2: 记忆系统

> 目标：让 Agent 具备跨会话的长期记忆、运行摘要回顾、以及上下文智能管理能力。

#### 2.1 记忆数据模型

```python
# packages/memory/models.py

@dataclass
class MemoryEntry:
    id: str
    user_id: str
    content: str                  # 记忆内容
    memory_type: MemoryType       # episode / semantic / procedural
    source: str                   # "run_summary" / "user_feedback" / "tool_observation"
    tags: list[str]
    importance: float             # 0.0~1.0 重要性评分
    access_count: int             # 被检索次数
    last_accessed: float
    created_at: float
    metadata: dict[str, Any]      # run_id, session_id, 关联上下文

class MemoryType(Enum):
    EPISODE = "episode"           # 事件记忆：某次运行发生了什么
    SEMANTIC = "semantic"         # 语义记忆：用户偏好、项目知识
    PROCEDURAL = "procedural"     # 过程记忆：成功的操作序列、解题模版
```

#### 2.2 记忆存储层

```python
# packages/memory/store.py

class MemoryStore(Protocol):
    async def save(self, entry: MemoryEntry) -> None: ...
    async def search(self, query: str, user_id: str, limit: int, filters: dict) -> list[MemoryEntry]: ...
    async def list_recent(self, user_id: str, limit: int) -> list[MemoryEntry]: ...
    async def delete(self, memory_id: str) -> None: ...
    async def update_access(self, memory_id: str) -> None: ...

class FileMemoryStore(MemoryStore):
    """JSON文件存储，v1 先用这个"""

class VectorMemoryStore(MemoryStore):
    """向量数据库存储，后续升级用"""
```

#### 2.3 记忆管理器

```python
# packages/memory/manager.py

class MemoryManager:
    async def remember_run(self, state: AgentState) -> None:
        """运行结束后自动提取摘要存入记忆"""
    
    async def recall(self, query: str, user_id: str, context: dict) -> list[MemoryEntry]:
        """根据当前任务检索相关记忆"""
    
    async def forget(self, memory_id: str) -> None:
        """遗忘不再重要的记忆"""
    
    async def consolidate(self, user_id: str) -> None:
        """整合/压缩相似记忆"""
```

#### 2.4 上下文管理器（增强版）

```python
# packages/memory/context_manager.py

class ContextManager:
    """管理对话上下文窗口，避免 token 爆炸"""
    
    async def build_context(self, state: AgentState) -> list[Message]:
        """
        策略：
        1. SystemPrompt (固定)
        2. 相关长期记忆 (recall 检索)
        3. 当前任务摘要 (如果历史太长则压缩)
        4. 最近 N 轮完整对话 (滑动窗口)
        """
    
    async def summarize_history(self, messages: list[Message]) -> str:
        """用 LLM 压缩过长的历史"""
    
    def estimate_tokens(self, messages: list[Message]) -> int:
        """估算 token 数"""
```

#### 任务清单

| # | 任务 |
|---|------|
| 2.1 | 创建 `memory/models.py` — MemoryEntry, MemoryType |
| 2.2 | 创建 `memory/store.py` — FileMemoryStore (JSON 实现) |
| 2.3 | 创建 `memory/manager.py` — MemoryManager |
| 2.4 | 创建 `memory/context_manager.py` — ContextManager |
| 2.5 | 集成到 AgentRuntime — 运行结束自动记忆 |
| 2.6 | 集成到 MessageBuilder — 构建消息时注入相关记忆 |
| 2.7 | 扩展 main.py show-memory 命令 |
| 2.8 | 编写测试 |

---

### Phase 3: 任务拆分 + 工作流规划

> 目标：让 Agent 能把复杂任务拆成子步骤，先想后做，具备 Plan → Execute → Verify 的能力。

#### 3.1 计划器

```python
# packages/planner/models.py

@dataclass
class TaskPlan:
    plan_id: str
    goal: str                     # 用户原始目标
    steps: list[TaskStep]         # 拆分后的子任务
    status: PlanStatus            # draft / executing / completed / failed
    created_at: float
    metadata: dict[str, Any]

@dataclass
class TaskStep:
    step_id: str
    description: str              # 子任务描述
    expected_tools: list[str]     # 预期使用的工具
    dependencies: list[str]       # 依赖的其他 step_id
    status: StepStatus            # pending / running / completed / failed / skipped
    result_summary: str | None    # 执行结果摘要
    verification: str | None      # 验证条件

class PlanStatus(Enum):
    DRAFT = "draft"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    REPLANNING = "replanning"
```

#### 3.2 计划器引擎

```python
# packages/planner/planner.py

class Planner:
    async def create_plan(self, goal: str, context: dict) -> TaskPlan:
        """让 LLM 分析目标并生成执行计划"""
    
    async def replan(self, plan: TaskPlan, feedback: str) -> TaskPlan:
        """根据执行反馈调整计划"""
    
    async def should_replan(self, plan: TaskPlan, current_step: TaskStep, result: Any) -> bool:
        """判断是否需要重新规划"""
```

#### 3.3 编排器

```python
# packages/orchestrator/orchestrator.py

class Orchestrator:
    """多轮自主执行引擎"""
    
    async def orchestrate(self, user_id: str, session_id: str, goal: str, max_turns: int) -> OrchestrationResult:
        """
        执行流程:
        1. Planner.create_plan(goal)
        2. 按依赖顺序遍历 steps
        3. 每个 step 调用 AgentRuntime.chat() 执行
        4. 执行后检查是否需要 replan
        5. 所有 step 完成 → 汇总最终输出
        """
    
    async def _execute_step(self, step: TaskStep, plan: TaskPlan) -> StepResult:
        """执行单个子任务"""
    
    async def _verify_step(self, step: TaskStep, result: StepResult) -> bool:
        """验证子任务结果"""

@dataclass
class OrchestrationResult:
    plan: TaskPlan
    turns: list[TurnRecord]
    completed: bool
    final_output: str
```

#### 任务清单

| # | 任务 |
|---|------|
| 3.1 | 创建 `planner/models.py` — TaskPlan, TaskStep |
| 3.2 | 创建 `planner/planner.py` — Planner (LLM 规划) |
| 3.3 | 创建 `planner/prompts.py` — 规划提示词模板 |
| 3.4 | 创建 `orchestrator/orchestrator.py` — Orchestrator |
| 3.5 | 创建 `orchestrator/models.py` — OrchestrationResult, TurnRecord |
| 3.6 | 集成到 AgentRuntime — orchestrate() 方法 |
| 3.7 | 完善 main.py orchestrate 命令 |
| 3.8 | 编写测试 |

---

### Phase 4: 增强 Loop 能力 + 提示词工程

> 目标：让循环更智能 —— 支持并行工具调用、自我反思、退化处理、以及更精细的提示词策略。

#### 4.1 并行工具调用

```python
# 在 agent_loop.py 的 _continue 中

# 当前: 每步只处理 tool_calls[0]
# 改进: 支持 batch 执行多个独立工具调用
async def _execute_parallel_tools(self, state, tool_calls):
    """并行执行无依赖的工具调用"""
    tasks = [self.tool_executor.execute(state, call) for call in tool_calls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ...
```

#### 4.2 自我反思机制

```python
# packages/runtime/reflection.py

class ReflectionEngine:
    async def reflect_on_failure(self, state: AgentState, error: str) -> str:
        """工具失败后，让 LLM 分析原因并建议下一步"""
    
    async def reflect_on_progress(self, state: AgentState) -> str:
        """定期回顾进展，判断是否偏离目标"""
    
    async def reflect_on_completion(self, state: AgentState) -> str:
        """任务完成前的最终自检"""
```

#### 4.3 循环检测增强

```python
# 增强 SemanticLoopDetector

class LoopDetector:
    def is_looping(self, state, tool_calls, content) -> bool:
        """现有: 简单签名比较"""
    
    def suggest_escape(self, state) -> str:
        """新增: 检测到循环时建议逃逸策略"""
    
    def detect_oscillation(self, state) -> bool:
        """新增: 检测在两个状态间来回切换"""
```

#### 4.4 提示词工程体系

```python
# packages/runtime/prompts/

base_system.py          # 基础系统提示词
coding_strategy.py      # 编码任务策略提示词
research_strategy.py    # 调研任务策略提示词
react_template.py       # ReAct 格式模版
cot_template.py         # Chain-of-Thought 模版
reflection_prompt.py    # 自我反思提示词
planning_prompt.py      # 任务规划提示词
```

**提示词策略选择器:**

```python
class PromptStrategy:
    def select(self, task: str, context: dict) -> str:
        """根据任务类型自动选择最佳提示词策略"""
        if self._is_coding_task(task):
            return CodingStrategy.build(context)
        if self._is_research_task(task):
            return ResearchStrategy.build(context)
        return DefaultStrategy.build(context)
```

#### 4.5 退化（Degraded）处理

```python
# 当预算耗尽但任务未完成时

class DegradedHandler:
    async def handle(self, state: AgentState) -> AgentState:
        """
        1. 收集已有的部分结果
        2. 让 LLM 生成"尽力回答"
        3. 标记 status=DEGRADED
        4. 保存中间成果供后续恢复
        """
```

#### 任务清单

| # | 任务 |
|---|------|
| 4.1 | 支持并行工具调用 (batch execute) |
| 4.2 | 实现反思引擎 (ReflectionEngine) |
| 4.3 | 增强循环检测 + 逃逸策略 |
| 4.4 | 建立提示词模版体系 |
| 4.5 | 实现提示词策略选择器 |
| 4.6 | 实现退化处理 (DegradedHandler) |
| 4.7 | 编写测试 |

---

### Phase 5: 高级能力

> 目标：向生产级 Code Agent 迈进 —— 多 Agent、代码理解、交互界面。

#### 5.1 多 Agent 协作 (P2)

```
Coordinator Agent  ─── 负责任务分配和汇总
    ├── Coder Agent      ─── 负责代码编写和修改
    ├── Reviewer Agent   ─── 负责代码审查
    ├── Researcher Agent ─── 负责信息检索
    └── Tester Agent     ─── 负责测试生成和执行
```

```python
# packages/multi_agent/coordinator.py

class AgentCoordinator:
    async def dispatch(self, task: str, agents: dict[str, AgentRuntime]) -> str:
        """将任务分配给最合适的 agent"""
    
    async def merge_results(self, results: dict[str, Any]) -> str:
        """合并多个 agent 的结果"""
```

#### 5.2 代码理解增强 (P2)

```python
# packages/code_intelligence/

class CodeAnalyzer:
    async def parse_ast(self, file_path: str) -> ASTInfo:
        """解析代码 AST"""
    
    async def find_references(self, symbol: str, scope: str) -> list[Reference]:
        """查找符号引用"""
    
    async def get_call_graph(self, function: str) -> CallGraph:
        """生成函数调用图"""
```

#### 5.3 交互界面 (P2)

- Web UI (基于 FastAPI + WebSocket)
- 实时事件流展示
- 工具执行审批界面
- 会话历史浏览
- 记忆管理面板


