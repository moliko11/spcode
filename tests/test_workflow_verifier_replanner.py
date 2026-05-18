from __future__ import annotations

import sys

from packages.workflow.models import WorkflowTaskStatus
from packages.workflow.replanner import Replanner
from packages.workflow.service import WorkflowService
from packages.workflow.store import WorkflowStore
from packages.workflow.verifier import Verifier


def test_verifier_checks_acceptance_criteria_and_test_command(tmp_path) -> None:
    service = WorkflowService(WorkflowStore(tmp_path / "workflows"))
    workflow, task, _ = service.create_task(
        {
            "task_id": "verify-me",
            "title": "Verify me",
            "acceptance_criteria": ["implemented"],
        }
    )
    workflow, task = service.update_task(
        {
            "workflow_id": workflow.workflow_id,
            "task_id": task.task_id,
            "result_summary": "implemented and tested",
        }
    )

    verifier = Verifier(workspace_root=tmp_path)
    result = verifier.verify_task(
        task,
        test_command=f'"{sys.executable}" -c "print(123)"',
    )

    assert result.ok is True
    assert "implemented" in result.passed
    assert result.evidence[0].metadata["exit_code"] == 0


def test_verifier_reports_missing_criteria(tmp_path) -> None:
    service = WorkflowService(WorkflowStore(tmp_path / "workflows"))
    workflow, task, _ = service.create_task(
        {
            "task_id": "verify-missing",
            "title": "Verify missing",
            "acceptance_criteria": ["must mention this exact phrase"],
        }
    )

    result = Verifier().verify_task(task, result_summary="no match")

    assert result.ok is False
    assert result.failed == ["must mention this exact phrase"]


def test_replanner_appends_followup_for_failed_task(tmp_path) -> None:
    service = WorkflowService(WorkflowStore(tmp_path / "workflows"))
    workflow, task, _ = service.create_task({"task_id": "failed", "title": "Failed task"})
    workflow, task = service.update_task(
        {
            "workflow_id": workflow.workflow_id,
            "task_id": task.task_id,
            "status": "failed",
            "error": "tests failed",
        }
    )

    result = Replanner(service).replan_failed_task(
        workflow_id=workflow.workflow_id,
        failed_task_id=task.task_id,
        strategy="append",
        reason="fix failed tests",
        new_tasks=[{"task_id": "fix-tests", "title": "Fix tests"}],
    )

    loaded = service.load_workflow(workflow.workflow_id)
    followup = service.find_task(loaded, "fix-tests")
    assert result.added_task_ids == ["fix-tests"]
    assert followup.dependencies == ["failed"]
    assert followup.metadata["source"] == "replanner"


def test_replanner_replace_skips_failed_task(tmp_path) -> None:
    service = WorkflowService(WorkflowStore(tmp_path / "workflows"))
    workflow, task, _ = service.create_task({"task_id": "bad", "title": "Bad task"})
    service.update_task({"workflow_id": workflow.workflow_id, "task_id": "bad", "status": "failed"})

    result = Replanner(service).replan_failed_task(
        workflow_id=workflow.workflow_id,
        failed_task_id="bad",
        strategy="replace",
        new_tasks=[{"task_id": "replacement", "title": "Replacement"}],
    )

    loaded = service.load_workflow(workflow.workflow_id)
    assert result.replaced_task_id == "bad"
    assert service.find_task(loaded, "bad").status == WorkflowTaskStatus.SKIPPED
    assert service.find_task(loaded, "replacement").status == WorkflowTaskStatus.PENDING
