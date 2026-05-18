from __future__ import annotations

from packages.workflow.models import Artifact, Evidence, TaskAttempt, WorkflowRun, WorkflowTask, WorkflowTaskStatus
from packages.workflow.service import WorkflowService
from packages.workflow.store import WorkflowStore


def test_workflow_store_round_trips_full_model(tmp_path) -> None:
    store = WorkflowStore(tmp_path / "workflows")
    workflow = WorkflowRun(goal="ship workflow")
    workflow.tasks.append(
        WorkflowTask(
            task_id="task-a",
            title="Implement models",
            status=WorkflowTaskStatus.RUNNING,
            attempts=[TaskAttempt(task_id="task-a")],
            evidence=[Evidence(source_type="test", summary="unit test passed")],
            artifacts=[Artifact(artifact_type="file", path="packages/workflow/models.py", summary="models")],
        )
    )

    store.save(workflow)
    loaded = store.load(workflow.workflow_id)

    assert loaded is not None
    assert loaded.workflow_id == workflow.workflow_id
    assert loaded.tasks[0].status == WorkflowTaskStatus.RUNNING
    assert loaded.tasks[0].attempts[0].task_id == "task-a"
    assert loaded.tasks[0].evidence[0].summary == "unit test passed"
    assert loaded.tasks[0].artifacts[0].path == "packages/workflow/models.py"


def test_workflow_service_create_update_list_stop(tmp_path) -> None:
    service = WorkflowService(WorkflowStore(tmp_path / "workflows"))

    workflow, root, created = service.create_task({"task_id": "root", "title": "Root"})
    assert created is True
    assert root.status == WorkflowTaskStatus.PENDING

    workflow, child, _ = service.create_task(
        {
            "workflow_id": workflow.workflow_id,
            "task_id": "child",
            "title": "Child",
            "dependencies": ["root"],
        }
    )
    assert child.dependencies == ["root"]

    workflow, updated = service.update_task(
        {
            "workflow_id": workflow.workflow_id,
            "task_id": "child",
            "status": "completed",
            "result_summary": "done",
            "evidence": [{"source_type": "test", "summary": "pytest passed"}],
        }
    )
    assert updated.status == WorkflowTaskStatus.COMPLETED
    assert updated.result_summary == "done"
    assert updated.evidence[0].summary == "pytest passed"

    listed = service.list_tasks(workflow_id=workflow.workflow_id)
    assert [task.task_id for _, task in listed] == ["root", "child"]

    stopped = service.stop(workflow_id=workflow.workflow_id, reason="cancel remaining")
    assert stopped == [{"workflow_id": workflow.workflow_id, "task_id": "root"}]
