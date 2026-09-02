import json
from pathlib import Path
from typing import Any, Dict, List

from .agent import CodingAgent, SYSTEM_PROMPT
from .llm import ChatClient
from .tools import ToolBox


PLANNER_PROMPT = """You are the Planner Agent in a local coding-agent team.
Inspect the workspace when useful, but never modify files or run commands.
Create a concrete implementation plan for the user's programming task.
The plan must name practical steps and verification checks.
Finish only by calling submit_plan. Do not return a plain-text final answer.
"""

REVIEWER_PROMPT = """You are the Reviewer Agent in a local coding-agent team.
Independently verify the original task against the actual workspace files.
Do not trust the Executor summary without evidence. Read relevant files and run the appropriate tests.
You may not modify files. Report concrete, actionable issues.
Finish only by calling finish_review. Approve only after a test command succeeds.
"""

PLANNER_TOOLS = {"list_files", "read_file", "submit_plan"}
EXECUTOR_TOOLS = {
    "list_files",
    "read_file",
    "write_file",
    "replace_in_file",
    "run_command",
    "finish_task",
}
REVIEWER_TOOLS = {"list_files", "read_file", "run_command", "finish_review"}


class MultiAgentCoordinator:
    def __init__(
        self,
        client: ChatClient,
        workspace: Path,
        timeout_seconds: int = 20,
        auto_approve: bool = False,
        enable_logging: bool = True,
        executor_max_steps: int = 20,
        planner_max_steps: int = 6,
        reviewer_max_steps: int = 8,
        max_repair_rounds: int = 1,
    ) -> None:
        self.client = client
        self.workspace = Path(workspace)
        self.timeout_seconds = timeout_seconds
        self.auto_approve = auto_approve
        self.enable_logging = enable_logging
        self.executor_max_steps = executor_max_steps
        self.planner_max_steps = planner_max_steps
        self.reviewer_max_steps = reviewer_max_steps
        self.max_repair_rounds = max_repair_rounds

    def run(self, task: str) -> str:
        print("\n=== Planner Agent ===")
        plan_result = self._planner().run_result(task)
        plan = plan_result.payload.get("plan")
        if not isinstance(plan, dict):
            raise RuntimeError("Planner did not return a structured plan.")
        print(f"Plan: {plan_result.summary}")

        print("\n=== Executor Agent ===")
        execution = self._executor().run_result(self._execution_task(task, plan))
        print(f"Execution: {execution.summary}")

        print("\n=== Reviewer Agent ===")
        review = self._reviewer().run_result(
            self._review_task(task, plan, execution.summary)
        )
        if self._approved(review.payload):
            print("Review: approved")
            return self._final_summary(plan_result.summary, execution.summary, review.summary)

        issues = self._issues(review.payload)
        print("Review: rejected")
        for repair_round in range(1, self.max_repair_rounds + 1):
            print(f"\n=== Executor Repair {repair_round}/{self.max_repair_rounds} ===")
            execution = self._executor().run_result(
                self._repair_task(task, plan, execution.summary, issues)
            )
            print(f"Execution: {execution.summary}")

            print(f"\n=== Reviewer Agent (round {repair_round + 1}) ===")
            review = self._reviewer().run_result(
                self._review_task(task, plan, execution.summary)
            )
            if self._approved(review.payload):
                print("Review: approved")
                return self._final_summary(plan_result.summary, execution.summary, review.summary)
            issues = self._issues(review.payload)
            print("Review: rejected")

        raise RuntimeError("Review failed after repair: " + "; ".join(issues))

    def _toolbox(self, role: str) -> ToolBox:
        return ToolBox(
            self.workspace,
            self.timeout_seconds,
            auto_approve=self.auto_approve,
            enable_logging=self.enable_logging,
            agent_role=role,
        )

    def _planner(self) -> CodingAgent:
        return CodingAgent(
            self.client,
            self._toolbox("planner"),
            self.planner_max_steps,
            system_prompt=PLANNER_PROMPT,
            agent_name="planner",
            allowed_tools=PLANNER_TOOLS,
            required_finish_tool="submit_plan",
        )

    def _executor(self) -> CodingAgent:
        return CodingAgent(
            self.client,
            self._toolbox("executor"),
            self.executor_max_steps,
            system_prompt=SYSTEM_PROMPT,
            agent_name="executor",
            allowed_tools=EXECUTOR_TOOLS,
            required_finish_tool="finish_task",
        )

    def _reviewer(self) -> CodingAgent:
        return CodingAgent(
            self.client,
            self._toolbox("reviewer"),
            self.reviewer_max_steps,
            system_prompt=REVIEWER_PROMPT,
            agent_name="reviewer",
            allowed_tools=REVIEWER_TOOLS,
            required_finish_tool="finish_review",
            required_successful_tools={"read_file", "run_command"},
        )

    def _execution_task(self, task: str, plan: Dict[str, Any]) -> str:
        return (
            "Original task:\n"
            + task
            + "\n\nPlanner output:\n"
            + json.dumps(plan, ensure_ascii=False, indent=2)
            + "\n\nImplement the task, run the requested checks, then call finish_task."
        )

    def _review_task(self, task: str, plan: Dict[str, Any], execution_summary: str) -> str:
        return (
            "Original task:\n"
            + task
            + "\n\nPlanner output:\n"
            + json.dumps(plan, ensure_ascii=False, indent=2)
            + "\n\nExecutor summary:\n"
            + execution_summary
            + "\n\nInspect the real files and independently run the relevant tests."
        )

    def _repair_task(
        self,
        task: str,
        plan: Dict[str, Any],
        execution_summary: str,
        issues: List[str],
    ) -> str:
        return (
            self._execution_task(task, plan)
            + "\n\nPrevious execution summary:\n"
            + execution_summary
            + "\n\nReviewer issues to fix:\n"
            + json.dumps(issues, ensure_ascii=False, indent=2)
        )

    def _approved(self, payload: Dict[str, Any]) -> bool:
        return payload.get("approved") is True

    def _issues(self, payload: Dict[str, Any]) -> List[str]:
        issues = payload.get("issues")
        if not isinstance(issues, list) or not issues:
            return ["Reviewer rejected the task without valid issues."]
        return [str(issue) for issue in issues]

    def _final_summary(self, plan: str, execution: str, review: str) -> str:
        return f"Plan: {plan}\nExecution: {execution}\nReview: approved - {review}"
