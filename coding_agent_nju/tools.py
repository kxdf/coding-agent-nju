import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List


class ToolBox:
    def __init__(self, workspace: Path, timeout_seconds: int = 20) -> None:
        self.workspace = Path(workspace).resolve()
        self.timeout_seconds = timeout_seconds
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
                    "description": "Run a shell command in the workspace and return exit code, stdout and stderr.",
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
            return json.dumps({"ok": False, "error": f"Invalid JSON arguments: {exc}"}, ensure_ascii=False)

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
            return json.dumps({"ok": False, "error": f"Unknown tool: {name}"}, ensure_ascii=False)

        try:
            result = handler(**arguments)
        except TypeError as exc:
            result = {"ok": False, "error": f"Bad tool arguments: {exc}"}
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
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
