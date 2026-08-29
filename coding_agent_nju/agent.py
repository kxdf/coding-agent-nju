from typing import Any, Dict, List

from .llm import ChatClient
from .tools import ToolBox


SYSTEM_PROMPT = """You are a coding agent running on the user's local machine.
You can inspect and modify files only through the provided tools.
Work in small steps: inspect files, write code, run commands or tests, and fix errors.
When the task is complete, call finish_task with a concise summary and mention important files changed.
Do not ask for secrets. Do not claim a command passed unless run_command showed success.
"""


class CodingAgent:
    def __init__(self, client: ChatClient, toolbox: ToolBox, max_steps: int = 12) -> None:
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
            message = response["choices"][0]["message"]
            messages.append(message)

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return message.get("content") or ""

            for tool_call in tool_calls:
                function = tool_call["function"]
                name = function["name"]
                arguments = function.get("arguments", "{}")
                print(f"[tool] {name} {arguments}")
                result = self.toolbox.call(name, arguments)
                print(f"[tool-result] {result[:500]}")
                if name == "finish_task":
                    return self._summary_from_tool_result(result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": result,
                    }
                )

        return "Reached the maximum number of steps before the model produced a final answer."

    def _summary_from_tool_result(self, result_json: str) -> str:
        import json

        try:
            result = json.loads(result_json)
        except json.JSONDecodeError:
            return "Task finished."
        return str(result.get("summary") or "Task finished.")
