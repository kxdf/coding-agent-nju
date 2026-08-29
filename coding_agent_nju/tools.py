import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List


class ToolBox:
    HIGH_RISK_TOOLS = {"write_file", "replace_in_file", "run_command"}

    def __init__(
        self,
        workspace: Path,
        timeout_seconds: int = 20,
        require_confirmation: bool = True,
        auto_approve: bool = False,
        enable_logging: bool = True,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.timeout_seconds = timeout_seconds
        self.require_confirmation = require_confirmation
        self.auto_approve = auto_approve
        self.enable_logging = enable_logging
        self.workspace.mkdir(parents=True, exist_ok=True)

    def schemas(self) -> List[Dict[str, Any]]:
        return [
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
        ]

    def call(self, name: str, arguments_json: str) -> str:
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

        handlers: Dict[str, Callable[..., Dict[str, Any]]] = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "replace_in_file": self.replace_in_file,
            "run_command": self.run_command,
            "finish_task": self.finish_task,
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
        safe = dict(arguments)
        if name == "write_file" and "content" in safe:
            content = str(safe["content"])
            safe["content"] = {
                "chars": len(content),
                "preview": content[:120],
            }
        return safe

    def _result_preview(self, result: Dict[str, Any]) -> Dict[str, Any]:
        preview: Dict[str, Any] = {}
        for key, value in result.items():
            if key == "content":
                text = str(value)
                preview[key] = {"chars": len(text), "preview": text[:120]}
            elif key in {"stdout", "stderr", "error", "summary"}:
                text = str(value)
                preview[key] = text[:300]
            else:
                preview[key] = value
        return preview

    def _preview_arguments(self, name: str, arguments: Dict[str, Any]) -> str:
        return json.dumps(self._sanitize_arguments(name, arguments), ensure_ascii=False)[:500]
