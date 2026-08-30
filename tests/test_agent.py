import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout

from coding_agent_nju.agent import CodingAgent
from coding_agent_nju.tools import ToolBox


class FakeChatClient:
    """Return scripted model responses so agent-loop tests need no network or API key."""

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


class CodingAgentTests(unittest.TestCase):
    def test_tool_result_is_added_to_history_before_next_model_call(self):
        responses = [
            assistant_message(tool_calls=[tool_call("call-write", "write_file", {"path": "answer.py", "content": "value = 42\n"})]),
            assistant_message(tool_calls=[tool_call("call-finish", "finish_task", {"summary": "created answer.py"})]),
        ]

        with tempfile.TemporaryDirectory() as directory:
            client = FakeChatClient(responses)
            tools = ToolBox(directory, auto_approve=True, enable_logging=False)
            agent = CodingAgent(client, tools, max_steps=5)

            with redirect_stdout(io.StringIO()):
                answer = agent.run("Create answer.py")

            self.assertEqual(answer, "created answer.py")
            self.assertEqual((tools.workspace / "answer.py").read_text(encoding="utf-8"), "value = 42\n")
            second_request_messages = client.calls[1]["messages"]
            tool_messages = [message for message in second_request_messages if message["role"] == "tool"]
            self.assertEqual(len(tool_messages), 1)
            self.assertEqual(tool_messages[0]["tool_call_id"], "call-write")
            self.assertTrue(json.loads(tool_messages[0]["content"])["ok"])

    def test_plain_assistant_message_finishes_without_tools(self):
        client = FakeChatClient([assistant_message(content="Nothing to change.")])

        with tempfile.TemporaryDirectory() as directory:
            agent = CodingAgent(client, ToolBox(directory, enable_logging=False))
            with redirect_stdout(io.StringIO()):
                answer = agent.run("Inspect the task")

        self.assertEqual(answer, "Nothing to change.")
        self.assertEqual(len(client.calls), 1)

    def test_step_limit_requests_a_final_summary_without_tools(self):
        client = FakeChatClient(
            [
                assistant_message(tool_calls=[tool_call("call-list", "list_files", {"path": "."})]),
                assistant_message(content="Stopped after checking the workspace."),
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            agent = CodingAgent(client, ToolBox(directory, enable_logging=False), max_steps=1)
            with redirect_stdout(io.StringIO()):
                answer = agent.run("Keep working")

        self.assertEqual(answer, "Stopped after checking the workspace.")
        self.assertEqual(client.calls[1]["tools"], [])
        self.assertIn("Stop using tools", client.calls[1]["messages"][-1]["content"])

    def test_invalid_model_response_has_a_clear_error(self):
        client = FakeChatClient([{"unexpected": True}])

        with tempfile.TemporaryDirectory() as directory:
            agent = CodingAgent(client, ToolBox(directory, enable_logging=False))
            with self.assertRaisesRegex(RuntimeError, r"missing choices\[0\]"):
                with redirect_stdout(io.StringIO()):
                    agent.run("Run a task")

    def test_invalid_tool_call_has_a_clear_error(self):
        malformed_call = {"id": "call-bad", "function": {"arguments": "{}"}}
        client = FakeChatClient([assistant_message(tool_calls=[malformed_call])])

        with tempfile.TemporaryDirectory() as directory:
            agent = CodingAgent(client, ToolBox(directory, enable_logging=False))
            with self.assertRaisesRegex(RuntimeError, "missing function name"):
                with redirect_stdout(io.StringIO()):
                    agent.run("Run a task")


if __name__ == "__main__":
    unittest.main()
