from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Evidence, WorkflowTask


@dataclass
class VerificationResult:
    ok: bool
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "passed": self.passed,
            "failed": self.failed,
            "evidence": [item.to_dict() for item in self.evidence],
            "metadata": self.metadata,
        }


class Verifier:
    def __init__(self, workspace_root: str | Path = ".") -> None:
        self.workspace_root = Path(workspace_root).resolve()

    def verify_task(
        self,
        task: WorkflowTask,
        *,
        result_summary: str | None = None,
        test_command: str | None = None,
        timeout_s: int = 60,
    ) -> VerificationResult:
        passed: list[str] = []
        failed: list[str] = []
        evidence: list[Evidence] = []
        summary = result_summary if result_summary is not None else (task.result_summary or "")

        for criterion in task.acceptance_criteria:
            if self._criterion_passed(criterion, summary, task):
                passed.append(criterion)
            else:
                failed.append(criterion)

        if test_command:
            command_evidence, command_ok = self._run_test_command(test_command, timeout_s)
            evidence.append(command_evidence)
            if command_ok:
                passed.append(f"test command passed: {test_command}")
            else:
                failed.append(f"test command failed: {test_command}")

        ok = not failed
        return VerificationResult(
            ok=ok,
            passed=passed,
            failed=failed,
            evidence=evidence,
            metadata={"verified_at": time.time(), "test_command": test_command or ""},
        )

    def _criterion_passed(self, criterion: str, summary: str, task: WorkflowTask) -> bool:
        normalized = criterion.strip().lower()
        if not normalized:
            return True
        if normalized in summary.lower():
            return True
        for item in task.evidence:
            if normalized in item.summary.lower() or (item.content and normalized in item.content.lower()):
                return True
        return False

    def _run_test_command(self, command: str, timeout_s: int) -> tuple[Evidence, bool]:
        try:
            completed = subprocess.run(
                command,
                cwd=self.workspace_root,
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            output = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") or (
                (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            )
            return (
                Evidence(
                    source_type="test_command",
                    summary=f"timeout after {timeout_s}s: {command}",
                    content=output[:4000],
                    metadata={"command": command, "exit_code": None, "timeout_s": timeout_s},
                ),
                False,
            )
        output = (completed.stdout or completed.stderr or "").strip()
        evidence = Evidence(
            source_type="test_command",
            summary=f"exit_code={completed.returncode}: {command}",
            content=output[:4000],
            metadata={"command": command, "exit_code": completed.returncode},
        )
        return evidence, completed.returncode == 0
