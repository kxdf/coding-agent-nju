import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

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
    def __init__(
        self,
        client: ChatClient,
        toolbox: ToolBox,
        max_steps: int = 20,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        agent_name: str = "agent",
        allowed_tools: Optional[Set[str]] = None,
        required_finish_tool: Optional[str] = None,
        required_successful_tools: Optional[Set[str]] = None,
    ) -> None:
        self.client = client
        self.toolbox = toolbox
        self.max_steps = max_steps
        self.system_prompt = system_prompt
        self.agent_name = agent_name
        self.allowed_tools = allowed_tools
        self.required_finish_tool = required_finish_tool
        self.required_successful_tools = required_successful_tools or set()

    def run(self, task: str) -> str:
        return self.run_result(task).summary

    def run_result(self, task: str) -> "AgentResult":
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": task},
        ]
        successful_tools: Set[str] = set()

        for step in range(1, self.max_steps + 1):
            print(f"\n[{self.agent_name}] step {step}/{self.max_steps}")
            response = self.client.complete(messages, self.toolbox.schemas(self.allowed_tools))
            message = self._response_message(response)
            messages.append(message)

            tool_calls = message.get("tool_calls")
            if tool_calls is None:
                tool_calls = []
            if not isinstance(tool_calls, list):
                raise RuntimeError("Invalid model response: tool_calls must be a list.")
            if not tool_calls:
                if self.required_finish_tool:
                    raise RuntimeError(
                        f"{self.agent_name} stopped without calling {self.required_finish_tool}."
                    )
                summary = message.get("content") or ""
                return AgentResult(summary, {}, "text", step)

            for tool_call in tool_calls:
                tool_call_id, name, arguments = self._tool_call_parts(tool_call)
                prefix = "tool" if self.agent_name == "agent" else f"{self.agent_name}][tool"
                print(f"[{prefix}] {name} {self.toolbox.preview_call(name, arguments)}")
                result_json = self.toolbox.call(name, arguments, self.allowed_tools)
                print(f"[{prefix}-result] {self.toolbox.preview_result(result_json)}")
                result = self._tool_result(result_json)
                if result.get("ok"):
                    successful_tools.add(name)
                if result.get("finished"):
                    if self.required_finish_tool and name != self.required_finish_tool:
                        raise RuntimeError(
                            f"{self.agent_name} used {name} instead of {self.required_finish_tool}."
                        )
                    missing = self.required_successful_tools - successful_tools
                    if missing:
                        result_json = json.dumps(
                            {
                                "ok": False,
                                "error": "Required successful tools before finishing: "
                                + ", ".join(sorted(missing)),
                            },
                            ensure_ascii=False,
                        )
                    else:
                        summary = str(result.get("summary") or "Task finished.")
                        return AgentResult(summary, result, "tool", step)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result_json,
                    }
                )

        if self.required_finish_tool:
            raise RuntimeError(
                f"{self.agent_name} reached the step limit without calling {self.required_finish_tool}."
            )
        summary = self._finalize_without_tools(messages)
        return AgentResult(summary, {}, "step_limit", self.max_steps)

    def _tool_result(self, result_json: str) -> Dict[str, Any]:
        try:
            result = json.loads(result_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid tool result JSON: {exc}") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Invalid tool result: expected an object.")
        return result

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


@dataclass(frozen=True)
class AgentResult:
    summary: str
    payload: Dict[str, Any]
    termination: str
    steps: int
