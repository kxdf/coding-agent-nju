from typing import Any, Dict, List, Tuple

from .llm import ChatClient
from .tools import ToolBox


SYSTEM_PROMPT = """You are a coding agent running on the user's local machine.
You can inspect and modify files only through the provided tools.
Work in small steps: inspect files, write code, run commands or tests, and fix errors.
Use write_file or replace_in_file for file edits. Use run_command mainly for commands and tests.
run_command already runs inside the workspace; do not use cd, absolute paths, or path-changing shell commands.
After the requested tests pass, call finish_task immediately with a concise summary.
Do not ask for secrets. Do not claim a command passed unless run_command showed success.
"""


class CodingAgent:
    def __init__(self, client: ChatClient, toolbox: ToolBox, max_steps: int = 20) -> None:
        self.client = client
        self.toolbox = toolbox
        self.max_steps = max_steps

    def run(self, task: str) -> str:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]

        for step in range(1, self.max_steps + 1):
            print(f"\n[agent] step {step}/{self.max_steps}")
            response = self.client.complete(messages, self.toolbox.schemas())
            message = self._response_message(response)
            messages.append(message)

            tool_calls = message.get("tool_calls")
            if tool_calls is None:
                tool_calls = []
            if not isinstance(tool_calls, list):
                raise RuntimeError("Invalid model response: tool_calls must be a list.")
            if not tool_calls:
                return message.get("content") or ""

            for tool_call in tool_calls:
                tool_call_id, name, arguments = self._tool_call_parts(tool_call)
                print(f"[tool] {name} {self.toolbox.preview_call(name, arguments)}")
                result = self.toolbox.call(name, arguments)
                print(f"[tool-result] {self.toolbox.preview_result(result)}")
                if name == "finish_task":
                    return self._summary_from_tool_result(result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result,
                    }
                )

        return self._finalize_without_tools(messages)

    def _summary_from_tool_result(self, result_json: str) -> str:
        import json

        try:
            result = json.loads(result_json)
        except json.JSONDecodeError:
            return "Task finished."
        return str(result.get("summary") or "Task finished.")

    def _finalize_without_tools(self, messages: List[Dict[str, Any]]) -> str:
        messages.append(
            {
                "role": "user",
                "content": "Stop using tools. Summarize what was completed and what checks passed.",
            }
        )
        response = self.client.complete(messages, [])
        message = self._response_message(response)
        return message.get("content") or "Task stopped after the configured step limit."

    def _response_message(self, response: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(response, dict):
            raise RuntimeError("Invalid model response: expected a JSON object.")
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("Invalid model response: missing choices[0].")
        first_choice = choices[0]
        if not isinstance(first_choice, dict) or not isinstance(first_choice.get("message"), dict):
            raise RuntimeError("Invalid model response: missing choices[0].message.")
        return first_choice["message"]

    def _tool_call_parts(self, tool_call: Any) -> Tuple[str, str, str]:
        if not isinstance(tool_call, dict):
            raise RuntimeError("Invalid tool call: expected an object.")
        tool_call_id = tool_call.get("id")
        function = tool_call.get("function")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise RuntimeError("Invalid tool call: missing id.")
        if not isinstance(function, dict):
            raise RuntimeError("Invalid tool call: missing function.")
        name = function.get("name")
        arguments = function.get("arguments", "{}")
        if not isinstance(name, str) or not name:
            raise RuntimeError("Invalid tool call: missing function name.")
        if not isinstance(arguments, str):
            raise RuntimeError("Invalid tool call: function arguments must be a JSON string.")
        return tool_call_id, name, arguments
