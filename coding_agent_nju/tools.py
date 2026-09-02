import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set


class ToolBox:
    DEFAULT_TOOL_NAMES = {
        "list_files",
        "read_file",
        "write_file",
        "replace_in_file",
        "run_command",
        "finish_task",
    }
    HIGH_RISK_TOOLS = {"write_file", "replace_in_file", "run_command"}
    SENSITIVE_NAMES = {"api_key", "apikey", "authorization", "password", "secret", "token"}

    def __init__(
        self,
        workspace: Path,
        timeout_seconds: int = 20,
        require_confirmation: bool = True,
        auto_approve: bool = False,
        enable_logging: bool = True,
        agent_role: str = "agent",
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.timeout_seconds = timeout_seconds
        self.require_confirmation = require_confirmation
        self.auto_approve = auto_approve
        self.enable_logging = enable_logging
        self.agent_role = agent_role
        self.workspace.mkdir(parents=True, exist_ok=True)

    def schemas(self, allowed_names: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
        schemas = [
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "List files under a directory inside the workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Relative directory path. Defaults to ."}
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a UTF-8 text file from the workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write a UTF-8 text file inside the workspace, creating parent folders if needed.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "replace_in_file",
                    "description": "Replace exact text in a UTF-8 file inside the workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old": {"type": "string"},
                            "new": {"type": "string"},
                        },
                        "required": ["path", "old", "new"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": "Run commands or tests in the workspace and return exit code, stdout and stderr. The command already runs in the workspace; do not use cd or absolute workspace paths. Prefer write_file for creating files.",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finish_task",
                    "description": "Finish the task after the requested code changes and checks are complete.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string", "description": "Short summary of completed work."}
                        },
                        "required": ["summary"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "submit_plan",
                    "description": "Submit the final structured implementation plan.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "goal": {"type": "string"},
                            "steps": {"type": "array", "items": {"type": "string"}},
                            "checks": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["goal", "steps", "checks"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finish_review",
                    "description": "Finish an independent review with an approval decision and concrete issues.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "approved": {"type": "boolean"},
                            "summary": {"type": "string"},
                            "issues": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["approved", "summary", "issues"],
                    },
                },
            },
        ]
        names = self.DEFAULT_TOOL_NAMES if allowed_names is None else allowed_names
        return [schema for schema in schemas if schema["function"]["name"] in names]

    def call(
        self,
        name: str,
        arguments_json: str,
        allowed_names: Optional[Set[str]] = None,
    ) -> str:
        try:
            arguments = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as exc:
            result = {"ok": False, "error": f"Invalid JSON arguments: {exc}"}
            self._log_tool_call(
                name,
                {"raw_arguments": (arguments_json or "")[:120], "raw_chars": len(arguments_json or "")},
                approved=False,
                blocked=False,
                result=result,
            )
            return json.dumps(result, ensure_ascii=False)

        if allowed_names is not None and name not in allowed_names:
            result = {
                "ok": False,
                "blocked": True,
                "error": f"Tool not allowed for {self.agent_role}: {name}",
            }
            self._log_tool_call(name, arguments, approved=False, blocked=True, result=result)
            return json.dumps(result, ensure_ascii=False)

        handlers: Dict[str, Callable[..., Dict[str, Any]]] = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "replace_in_file": self.replace_in_file,
            "run_command": self.run_command,
            "finish_task": self.finish_task,
            "submit_plan": self.submit_plan,
            "finish_review": self.finish_review,
        }
        handler = handlers.get(name)
        if handler is None:
            result = {"ok": False, "error": f"Unknown tool: {name}"}
            self._log_tool_call(name, arguments, approved=False, blocked=False, result=result)
            return json.dumps(result, ensure_ascii=False)

        approved = True
        blocked = False
        if name == "run_command":
            command_error = self._command_policy_error(str(arguments.get("command", "")))
            if command_error:
                blocked = True
                result = {"ok": False, "blocked": True, "error": f"Command blocked by safety policy: {command_error}"}
                self._log_tool_call(name, arguments, approved=False, blocked=blocked, result=result)
                return json.dumps(result, ensure_ascii=False)

        if self._needs_confirmation(name):
            approved = self._confirm_tool_call(name, arguments)
            if not approved:
                result = {"ok": False, "denied": True, "error": "User denied tool execution."}
                self._log_tool_call(name, arguments, approved=approved, blocked=blocked, result=result)
                return json.dumps(result, ensure_ascii=False)

        try:
            result = handler(**arguments)
        except TypeError as exc:
            result = {"ok": False, "error": f"Bad tool arguments: {exc}"}
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        self._log_tool_call(name, arguments, approved=approved, blocked=blocked, result=result)
        return json.dumps(result, ensure_ascii=False)

    def preview_call(self, name: str, arguments_json: str) -> str:
        try:
            arguments = json.loads(arguments_json or "{}")
        except json.JSONDecodeError:
            return self._redact_text(arguments_json or "")[:500]
        if not isinstance(arguments, dict):
            return self._redact_text(str(arguments))[:500]
        return self._preview_arguments(name, arguments)

    def preview_result(self, result_json: str) -> str:
        try:
            result = json.loads(result_json)
        except json.JSONDecodeError:
            return self._redact_text(result_json)[:500]
        if not isinstance(result, dict):
            return self._redact_text(str(result))[:500]
        return json.dumps(self._result_preview(result), ensure_ascii=False)[:500]

    def list_files(self, path: str = ".") -> Dict[str, Any]:
        root = self._safe_path(path)
        if not root.exists():
            return {"ok": False, "error": f"Path does not exist: {path}"}
        if root.is_file():
            return {"ok": True, "files": [str(root.relative_to(self.workspace))]}

        files = []
        for child in sorted(root.rglob("*")):
            if child.is_file():
                files.append(str(child.relative_to(self.workspace)))
        return {"ok": True, "files": files}

    def read_file(self, path: str) -> Dict[str, Any]:
        target = self._safe_path(path)
        if not target.is_file():
            return {"ok": False, "error": f"Not a file: {path}"}
        return {"ok": True, "content": target.read_text(encoding="utf-8")}

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(target.relative_to(self.workspace)), "bytes": len(content.encode("utf-8"))}

    def replace_in_file(self, path: str, old: str, new: str) -> Dict[str, Any]:
        target = self._safe_path(path)
        if not target.is_file():
            return {"ok": False, "error": f"Not a file: {path}"}
        content = target.read_text(encoding="utf-8")
        if old not in content:
            return {"ok": False, "error": "Old text was not found."}
        target.write_text(content.replace(old, new), encoding="utf-8")
        return {"ok": True, "path": str(target.relative_to(self.workspace))}

    def run_command(self, command: str) -> Dict[str, Any]:
        completed = subprocess.run(
            command,
            cwd=str(self.workspace),
            shell=True,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-6000:],
            "stderr": completed.stderr[-6000:],
        }

    def finish_task(self, summary: str) -> Dict[str, Any]:
        return {"ok": True, "finished": True, "summary": summary}

    def submit_plan(self, goal: str, steps: List[str], checks: List[str]) -> Dict[str, Any]:
        if not isinstance(goal, str) or not goal.strip():
            return {"ok": False, "error": "Plan goal must be a non-empty string."}
        if not isinstance(steps, list) or not steps or not all(isinstance(item, str) and item.strip() for item in steps):
            return {"ok": False, "error": "Plan steps must be a non-empty list of strings."}
        if not isinstance(checks, list) or not checks or not all(isinstance(item, str) and item.strip() for item in checks):
            return {"ok": False, "error": "Plan checks must be a non-empty list of strings."}
        plan = {"goal": goal.strip(), "steps": steps, "checks": checks}
        return {"ok": True, "finished": True, "summary": goal.strip(), "plan": plan}

    def finish_review(self, approved: bool, summary: str, issues: List[str]) -> Dict[str, Any]:
        if not isinstance(approved, bool):
            return {"ok": False, "error": "Review approved must be a boolean."}
        if not isinstance(summary, str) or not summary.strip():
            return {"ok": False, "error": "Review summary must be a non-empty string."}
        if not isinstance(issues, list) or not all(isinstance(item, str) and item.strip() for item in issues):
            return {"ok": False, "error": "Review issues must be a list of non-empty strings."}
        if approved and issues:
            return {"ok": False, "error": "An approved review must not contain issues."}
        if not approved and not issues:
            return {"ok": False, "error": "A rejected review must contain at least one issue."}
        return {
            "ok": True,
            "finished": True,
            "approved": approved,
            "summary": summary.strip(),
            "issues": issues,
        }

    def _safe_path(self, path: str) -> Path:
        candidate = (self.workspace / path).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise ValueError(f"Path escapes workspace: {path}")
        return candidate

    def _needs_confirmation(self, name: str) -> bool:
        return self.require_confirmation and not self.auto_approve and name in self.HIGH_RISK_TOOLS

    def _confirm_tool_call(self, name: str, arguments: Dict[str, Any]) -> bool:
        print("\nPermission required")
        print(f"Tool: {name}")
        print(f"Workspace: {self.workspace}")
        print(f"Arguments: {self._preview_arguments(name, arguments)}")
        answer = input("Allow this tool call? [y/N]: ").strip().lower()
        return answer in {"y", "yes"}

    def _command_policy_error(self, command: str) -> str:
        normalized = " ".join(command.strip().lower().split())
        if not normalized:
            return "empty command"

        blocked_substrings = [
            "del /s",
            "rmdir /s",
            "remove-item -recurse",
            "rm -rf",
            "shutdown",
            "format",
            "taskkill",
            "git push",
            "git reset --hard",
            "git clean",
            "curl ",
            "invoke-webrequest",
        ]
        for substring in blocked_substrings:
            if substring in normalized:
                if substring in {"curl ", "invoke-webrequest"} and "|" not in normalized:
                    continue
                return substring

        tokens = normalized.replace("&&", " ").replace(";", " ").split()
        if "cd" in tokens:
            return "cd is not allowed because commands already run in the workspace"
        if ".." in normalized:
            return "parent directory traversal is not allowed"
        if str(self.workspace).lower() in normalized:
            return "absolute workspace paths are not allowed"
        return ""

    def _log_tool_call(
        self,
        name: str,
        arguments: Dict[str, Any],
        approved: bool,
        blocked: bool,
        result: Dict[str, Any],
    ) -> None:
        if not self.enable_logging:
            return
        log_dir = self.workspace / ".agent_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_role": self.agent_role,
            "tool": name,
            "arguments": self._sanitize_arguments(name, arguments),
            "approved": approved,
            "blocked": blocked,
            "ok": bool(result.get("ok")),
            "result_preview": self._result_preview(result),
        }
        with (log_dir / "session.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _sanitize_arguments(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        safe = self._sanitize_value(arguments)
        if name == "write_file" and "content" in safe:
            content = str(safe["content"])
            original_content = str(arguments.get("content", ""))
            safe["content"] = self._text_summary(original_content, 120)
        if name == "replace_in_file":
            for field in ("old", "new"):
                if field in arguments:
                    safe[field] = self._text_summary(str(arguments[field]), 120)
        return safe

    def _result_preview(self, result: Dict[str, Any]) -> Dict[str, Any]:
        preview: Dict[str, Any] = {}
        for key, value in result.items():
            if key == "content":
                text = str(value)
                preview[key] = self._text_summary(text, 120)
            elif key in {"stdout", "stderr", "error", "summary"}:
                text = str(value)
                preview[key] = self._redact_text(text)[:300]
            else:
                preview[key] = self._sanitize_value(value)
        return preview

    def _preview_arguments(self, name: str, arguments: Dict[str, Any]) -> str:
        return json.dumps(self._sanitize_arguments(name, arguments), ensure_ascii=False)[:500]

    def _sanitize_value(self, value: Any, key: str = "") -> Any:
        normalized_key = key.lower().replace("-", "_")
        if any(name in normalized_key for name in self.SENSITIVE_NAMES):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(item_key): self._sanitize_value(item_value, str(item_key)) for item_key, item_value in value.items()}
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        if isinstance(value, str):
            return self._redact_text(value)
        return value

    def _text_summary(self, text: str, preview_chars: int) -> Dict[str, Any]:
        return {
            "chars": len(text),
            "preview": self._redact_text(text)[:preview_chars],
        }

    def _redact_text(self, text: str) -> str:
        redacted = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", text)
        assignment = re.compile(
            r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)([^\s,;}]+)"
        )
        return assignment.sub(r"\1[REDACTED]", redacted)
