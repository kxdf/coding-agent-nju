import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout

from coding_agent_nju.agent import CodingAgent
from coding_agent_nju.multi_agent import (
    PLANNER_PROMPT,
    PLANNER_TOOLS,
    REVIEWER_PROMPT,
    REVIEWER_TOOLS,
    MultiAgentCoordinator,
)
from coding_agent_nju.tools import ToolBox


class FakeChatClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, tools):
        self.calls.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        if not self.responses:
            raise AssertionError("FakeChatClient has no response left.")
        return self.responses.pop(0)


def assistant_message(content=None, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def tool_call(call_id, name, arguments):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def plan_response():
    return assistant_message(
        tool_calls=[
            tool_call(
                "plan",
                "submit_plan",
                {
                    "goal": "create a tested value module",
                    "steps": ["write implementation", "write tests", "run tests"],
                    "checks": ["python -m unittest test_value -v"],
                },
            )
        ]
    )


def review_response(call_id, approved, issues):
    return assistant_message(
        tool_calls=[
            tool_call(
                call_id,
                "finish_review",
                {
                    "approved": approved,
                    "summary": "review passed" if approved else "review failed",
                    "issues": issues,
                },
            )
        ]
    )


class MultiAgentTests(unittest.TestCase):
    def test_role_schemas_expose_only_allowed_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = ToolBox(directory, enable_logging=False)
            planner_names = {item["function"]["name"] for item in tools.schemas(PLANNER_TOOLS)}
            reviewer_names = {item["function"]["name"] for item in tools.schemas(REVIEWER_TOOLS)}

        self.assertEqual(planner_names, PLANNER_TOOLS)
        self.assertEqual(reviewer_names, REVIEWER_TOOLS)
        self.assertNotIn("write_file", reviewer_names)

    def test_unauthorized_tool_is_blocked_even_if_model_invents_it(self):
        responses = [
            assistant_message(
                tool_calls=[tool_call("bad-write", "write_file", {"path": "bad.txt", "content": "bad"})]
            ),
            plan_response(),
        ]
        with tempfile.TemporaryDirectory() as directory:
            client = FakeChatClient(responses)
            tools = ToolBox(directory, auto_approve=True, enable_logging=False, agent_role="planner")
            agent = CodingAgent(
                client,
                tools,
                max_steps=3,
                system_prompt=PLANNER_PROMPT,
                agent_name="planner",
                allowed_tools=PLANNER_TOOLS,
                required_finish_tool="submit_plan",
            )
            with redirect_stdout(io.StringIO()):
                result = agent.run_result("plan a task")

            self.assertFalse((tools.workspace / "bad.txt").exists())
            blocked = json.loads(client.calls[1]["messages"][-1]["content"])
            self.assertTrue(blocked["blocked"])
            self.assertIn("plan", result.payload)

    def test_reviewer_must_read_a_file_and_run_a_test_before_finishing(self):
        responses = [
            review_response("early-review", True, []),
            assistant_message(
                tool_calls=[tool_call("review-read", "read_file", {"path": "value.py"})]
            ),
            assistant_message(
                tool_calls=[tool_call("review-test", "run_command", {"command": "python -c \"print('ok')\""})]
            ),
            review_response("final-review", True, []),
        ]
        with tempfile.TemporaryDirectory() as directory:
            client = FakeChatClient(responses)
            tools = ToolBox(directory, auto_approve=True, enable_logging=False, agent_role="reviewer")
            (tools.workspace / "value.py").write_text("VALUE = 42\n", encoding="utf-8")
            agent = CodingAgent(
                client,
                tools,
                max_steps=4,
                system_prompt=REVIEWER_PROMPT,
                agent_name="reviewer",
                allowed_tools=REVIEWER_TOOLS,
                required_finish_tool="finish_review",
                required_successful_tools={"read_file", "run_command"},
            )
            with redirect_stdout(io.StringIO()):
                result = agent.run_result("review the task")

        self.assertTrue(result.payload["approved"])
        first_feedback = json.loads(client.calls[1]["messages"][-1]["content"])
        self.assertFalse(first_feedback["ok"])
        self.assertIn("run_command", first_feedback["error"])

    def test_successful_pipeline_runs_planner_executor_and_reviewer(self):
        responses = [
            plan_response(),
            assistant_message(
                tool_calls=[
                    tool_call("write-value", "write_file", {"path": "value.py", "content": "VALUE = 42\n"}),
                    tool_call(
                        "write-test",
                        "write_file",
                        {
                            "path": "test_value.py",
                            "content": "import unittest\nfrom value import VALUE\n\nclass T(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(VALUE, 42)\n",
                        },
                    ),
                ]
            ),
            assistant_message(
                tool_calls=[tool_call("executor-test", "run_command", {"command": "python -m unittest test_value -v"})]
            ),
            assistant_message(
                tool_calls=[tool_call("executor-finish", "finish_task", {"summary": "implementation passed"})]
            ),
            assistant_message(tool_calls=[tool_call("review-read", "read_file", {"path": "value.py"})]),
            assistant_message(
                tool_calls=[tool_call("review-test", "run_command", {"command": "python -m unittest test_value -v"})]
            ),
            review_response("review-finish", True, []),
        ]
        with tempfile.TemporaryDirectory() as directory:
            client = FakeChatClient(responses)
            coordinator = MultiAgentCoordinator(
                client,
                directory,
                auto_approve=True,
                enable_logging=False,
            )
            with redirect_stdout(io.StringIO()):
                answer = coordinator.run("create a value module")

            self.assertTrue((coordinator.workspace / "value.py").is_file())
            self.assertIn("Review: approved", answer)
            planner_tools = {item["function"]["name"] for item in client.calls[0]["tools"]}
            executor_tools = {item["function"]["name"] for item in client.calls[1]["tools"]}
            reviewer_tools = {item["function"]["name"] for item in client.calls[4]["tools"]}
            self.assertEqual(planner_tools, PLANNER_TOOLS)
            self.assertIn("write_file", executor_tools)
            self.assertEqual(reviewer_tools, REVIEWER_TOOLS)
            self.assertIn("Executor summary", client.calls[4]["messages"][1]["content"])

    def test_rejected_review_triggers_exactly_one_repair(self):
        responses = [
            plan_response(),
            assistant_message(
                tool_calls=[tool_call("executor-finish", "finish_task", {"summary": "first attempt"})]
            ),
            assistant_message(
                tool_calls=[tool_call("review-read-1", "read_file", {"path": "value.py"})]
            ),
            assistant_message(
                tool_calls=[tool_call("review-test-1", "run_command", {"command": "python -c \"print('checked')\""})]
            ),
            review_response("review-reject", False, ["missing fix.txt"]),
            assistant_message(
                tool_calls=[tool_call("repair-write", "write_file", {"path": "fix.txt", "content": "fixed\n"})]
            ),
            assistant_message(
                tool_calls=[tool_call("repair-finish", "finish_task", {"summary": "repair complete"})]
            ),
            assistant_message(
                tool_calls=[tool_call("review-read-2", "read_file", {"path": "fix.txt"})]
            ),
            assistant_message(
                tool_calls=[tool_call("review-test-2", "run_command", {"command": "python -c \"print('checked')\""})]
            ),
            review_response("review-approve", True, []),
        ]
        with tempfile.TemporaryDirectory() as directory:
            client = FakeChatClient(responses)
            with open(f"{directory}/value.py", "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            coordinator = MultiAgentCoordinator(
                client,
                directory,
                auto_approve=True,
                enable_logging=False,
                max_repair_rounds=1,
            )
            with redirect_stdout(io.StringIO()):
                answer = coordinator.run("repair after review")

            self.assertTrue((coordinator.workspace / "fix.txt").is_file())
            self.assertIn("Review: approved", answer)
            repair_task = client.calls[5]["messages"][1]["content"]
            self.assertIn("missing fix.txt", repair_task)

    def test_second_rejection_stops_without_another_repair(self):
        responses = [
            plan_response(),
            assistant_message(
                tool_calls=[tool_call("executor-finish", "finish_task", {"summary": "first attempt"})]
            ),
            assistant_message(
                tool_calls=[tool_call("review-read-1", "read_file", {"path": "value.py"})]
            ),
            assistant_message(
                tool_calls=[tool_call("review-test-1", "run_command", {"command": "python -c \"print('checked')\""})]
            ),
            review_response("review-reject-1", False, ["issue one"]),
            assistant_message(
                tool_calls=[tool_call("repair-finish", "finish_task", {"summary": "repair attempted"})]
            ),
            assistant_message(
                tool_calls=[tool_call("review-read-2", "read_file", {"path": "value.py"})]
            ),
            assistant_message(
                tool_calls=[tool_call("review-test-2", "run_command", {"command": "python -c \"print('checked')\""})]
            ),
            review_response("review-reject-2", False, ["issue remains"]),
        ]
        with tempfile.TemporaryDirectory() as directory:
            client = FakeChatClient(responses)
            with open(f"{directory}/value.py", "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            coordinator = MultiAgentCoordinator(
                client,
                directory,
                auto_approve=True,
                enable_logging=False,
                max_repair_rounds=1,
            )
            with self.assertRaisesRegex(RuntimeError, "issue remains"):
                with redirect_stdout(io.StringIO()):
                    coordinator.run("a task that remains invalid")

        self.assertEqual(len(client.calls), 9)

    def test_planner_plain_text_is_rejected(self):
        client = FakeChatClient([assistant_message(content="plain text plan")])
        with tempfile.TemporaryDirectory() as directory:
            coordinator = MultiAgentCoordinator(client, directory, enable_logging=False)
            with self.assertRaisesRegex(RuntimeError, "without calling submit_plan"):
                with redirect_stdout(io.StringIO()):
                    coordinator.run("plan a task")

    def test_reviewer_plain_text_is_rejected(self):
        responses = [
            plan_response(),
            assistant_message(
                tool_calls=[tool_call("executor-finish", "finish_task", {"summary": "done"})]
            ),
            assistant_message(content="looks good"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            client = FakeChatClient(responses)
            coordinator = MultiAgentCoordinator(client, directory, enable_logging=False)
            with self.assertRaisesRegex(RuntimeError, "without calling finish_review"):
                with redirect_stdout(io.StringIO()):
                    coordinator.run("review a task")

    def test_invalid_plan_is_returned_to_planner_for_correction(self):
        invalid_plan = assistant_message(
            tool_calls=[
                tool_call(
                    "invalid-plan",
                    "submit_plan",
                    {"goal": "goal", "steps": [], "checks": []},
                )
            ]
        )
        client = FakeChatClient([invalid_plan, plan_response()])
        with tempfile.TemporaryDirectory() as directory:
            agent = CodingAgent(
                client,
                ToolBox(directory, enable_logging=False, agent_role="planner"),
                max_steps=3,
                system_prompt=PLANNER_PROMPT,
                agent_name="planner",
                allowed_tools=PLANNER_TOOLS,
                required_finish_tool="submit_plan",
            )
            with redirect_stdout(io.StringIO()):
                result = agent.run_result("plan a task")

        feedback = json.loads(client.calls[1]["messages"][-1]["content"])
        self.assertFalse(feedback["ok"])
        self.assertIn("steps", feedback["error"])
        self.assertEqual(result.payload["plan"]["goal"], "create a tested value module")

    def test_multi_agent_log_contains_each_role(self):
        responses = [
            plan_response(),
            assistant_message(
                tool_calls=[tool_call("executor-finish", "finish_task", {"summary": "done"})]
            ),
            assistant_message(tool_calls=[tool_call("review-read", "read_file", {"path": "value.py"})]),
            assistant_message(
                tool_calls=[tool_call("review-test", "run_command", {"command": "python -c \"print('ok')\""})]
            ),
            review_response("review-finish", True, []),
        ]
        with tempfile.TemporaryDirectory() as directory:
            with open(f"{directory}/value.py", "w", encoding="utf-8") as handle:
                handle.write("VALUE = 42\n")
            coordinator = MultiAgentCoordinator(
                FakeChatClient(responses),
                directory,
                auto_approve=True,
                enable_logging=True,
            )
            with redirect_stdout(io.StringIO()):
                coordinator.run("inspect value.py")
            records = [
                json.loads(line)
                for line in (coordinator.workspace / ".agent_logs" / "session.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]

        self.assertEqual({record["agent_role"] for record in records}, {"planner", "executor", "reviewer"})


if __name__ == "__main__":
    unittest.main()
