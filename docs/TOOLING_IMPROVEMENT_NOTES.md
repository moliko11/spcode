# 工具体系改进记录

本文记录本轮对 IO / workflow 工具的阶段性改动、验证结果和后续缺口。

## 阶段提交

1. `1c74cc8 实现工作流任务工具并接入运行时`
   - 新增 `task_create`、`task_update`、`task_list`、`task_output`、`task_stop`。
   - 接入 `runtime/bootstrap.py`、`runtime/config.py` 和系统提示词。
   - 为 `PlanStore`、`PlanRunStore` 补 `list_recent()`。
   - 修复 `ListDirTool` 在 Windows 下使用未 resolve workspace 计算相对路径的问题。

2. `536ed1b 改善 Bash 工具错误输出`
   - `bash` 失败时返回具体 `error`，优先使用 stderr/stdout，而不是泛化的 `tool execution failed`。
   - 从 stdout 中移除内部 cwd marker `__CODEX_CWD__`。
   - `ToolExecutor` 规范化 dict 工具结果时，会保留失败工具的具体错误信息。

3. `b554db6 稳定 Grep 工具结构化解析`
   - `grep` 的 rg 路径从冒号切分改成 `rg --json` 解析。
   - 避免 Windows 绝对路径盘符导致解析错误。
   - 保留 Python fallback。

4. `b1bed41 加强任务工具状态校验`
   - task dependencies 必须存在于同一个 plan。
   - 同名 task 出现在多个 plan 时，更新/查询要求显式传入 `plan_id`。
   - 增加更严格的 task 状态流转校验。
   - `GuardrailEngine` 增加 `task_*` 参数基础校验。

## 已验证命令

```powershell
uv run python -m pytest tests\test_task_tools.py tests\w1_planner\test_plan_store.py tests\w3_approval\test_plan_run_store.py
uv run python -m pytest tests\test_runtime_bootstrap.py tests\test_tool_search_tool.py tests\test_api_routes.py
uv run python -m pytest tests\test_core_bash_tools.py tests\test_runtime_bootstrap.py
uv run python -m pytest tests\test_core_search_tools.py
uv run python -m pytest tests\test_task_tools.py tests\test_runtime_bootstrap.py
```

## 当前工具体系的主要缺口

### 通用工具层

- 还没有统一的 `BaseTool` 抽象、参数 schema 声明和结果渲染协议。
- `ToolSpec.parameters`、`GuardrailEngine.validate_tool_args()`、工具内部校验仍然分散。
- 工具错误还没有统一错误码、错误阶段和可恢复建议。
- 工具预算是全局计数，task tracking 这类低成本状态工具容易耗尽 `MAX_TOOL_CALLS`。

### 文件工具

- `file_edit` 只有 exact replace 和 line insert，缺少 patch/diff 应用能力。
- 缺少 dry-run、diff preview、批量编辑、回滚或备份 artifact。
- `file_write overwrite` 风险较高，但没有旧内容摘要、hash 或备份记录。
- `file_read` 对大文件只做字节截断，缺少分页读取、符号级读取和搜索上下文读取。

### 搜索工具

- `glob`/`grep` 的忽略规则是内置集合，尚未读取 `.gitignore`。
- `grep` 缺少上下文行、按文件聚合、分页继续读取和二进制/大文件统计。
- Python fallback 仍然逐文件读全文，后续需要加文件大小限制和编码探测。

### Bash 工具

- 模型仍容易生成不符合当前 shell 的命令，例如 PowerShell 下使用 Unix 风格组合命令。
- 还没有 read-only shell 与 write shell 的风险分级。
- `changed_files` 目前没有真实 diff 追踪。
- 审批 UI 里仍可能出现重复 approval 提示，需要梳理事件流。

### Web 工具

- 搜索 fallback HTML 解析脆弱，结果质量不稳定。
- `web_search` / `web_fetch` 还没有标准 Evidence 输出。
- 缺少内容类型识别、PDF/二进制处理、引用来源和日期约束。

### Task / Workflow 工具

- task 工具现在仍映射到 `TaskPlan.steps`，不是完整 `WorkflowStore`。
- `artifacts`、`evidence` 只是 metadata，没有独立数据模型和索引。
- task 与 orchestrator 的同步只覆盖部分场景，恢复、取消、重试和 replan 还需要统一状态源。
- 缺少 parent/child task 层级模型，当前只能用 dependencies 表达关系。

## 建议后续阶段

1. 建立统一 `ToolResult` / `ToolError` / `ToolArtifact` 协议。
2. 把工具 schema 与 guardrail 校验合并为单一声明源。
3. 将 `task_*` 工具迁移到 `packages/workflow/store.py`，以 WorkflowRun 为中心建模。
4. 增加 task/evidence/artifact 的独立模型和查询接口。
5. 给工具预算增加类别：state 工具、read 工具、write 工具、shell 工具分开计数。
6. 改造 shell 工具，支持 read-only 命令免审批或轻审批，写命令继续强审批。
7. 为文件编辑实现 diff preview + apply patch + rollback。
