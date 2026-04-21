"""
Scheduler — 基于 DAG 依赖关系，找出每一轮可并行执行的就绪步骤。

核心思路：
  "就绪" = status 为 PENDING  AND  所有依赖步骤都已 COMPLETED

调用方应在每一波执行完毕后再次调用 get_ready_steps()，
直到返回空列表为止（所有步骤完成或失败/跳过）。
"""
from __future__ import annotations

from packages.planner.models import StepStatus, TaskPlan, TaskStep


class Scheduler:
    """无状态调度器：纯函数式，不修改 plan 对象。"""

    def get_ready_steps(
        self,
        plan: TaskPlan,
        completed_ids: set[str],
    ) -> list[TaskStep]:
        """
        返回当前可立即执行的步骤列表（可并行）。

        条件：
        - step.status == PENDING
        - step 的所有 dependencies 都在 completed_ids 中

        :param plan: 当前 TaskPlan（只读）。
        :param completed_ids: 已完成步骤的 step_id 集合。
        :return: 可并行执行的步骤列表（顺序不保证）。
        """
        return [
            step
            for step in plan.steps
            if step.status == StepStatus.PENDING
            and all(dep in completed_ids for dep in step.dependencies)
        ]

    def has_pending(self, plan: TaskPlan) -> bool:
        """判断 plan 中是否还有 PENDING 状态的步骤。"""
        return any(s.status == StepStatus.PENDING for s in plan.steps)

    def is_stuck(self, plan: TaskPlan, completed_ids: set[str]) -> bool:
        """
        检测死锁：还有 PENDING 步骤，但没有任何步骤可以就绪
        （通常是依赖环路或所有前置步骤均失败/跳过）。
        """
        return self.has_pending(plan) and not self.get_ready_steps(plan, completed_ids)
