"""Sandboxed filesystem tools for the CLI.

Every path the model supplies is resolved and checked against the workspace root
before any operation runs. `Path.resolve()` expands `..` *and* follows symlinks, so
both `../../etc/passwd` and a symlink pointing outside the tree are rejected. Absolute
paths are rejected outright — pathlib's `/` operator lets an absolute right-hand side
silently replace the root, which would otherwise defeat the check.

With no workspace set, none of these tools are exposed to the model at all.
"""

import shutil
from pathlib import Path

MAX_READ_BYTES = 256_000
MAX_LIST_ENTRIES = 300

# Operations that change or destroy existing data. The CLI asks before running these;
# pure creation (new file, new directory) runs without a prompt.
DESTRUCTIVE = {"write_file", "edit_file", "delete_path", "move_path"}


class WorkspaceError(Exception):
    """Raised for a path outside the workspace or a failed operation."""


class Workspace:
    def __init__(self, root: str):
        p = Path(root).expanduser()
        if not p.exists():
            raise WorkspaceError(f"no such directory: {p}")
        if not p.is_dir():
            raise WorkspaceError(f"not a directory: {p}")
        self.root = p.resolve()

    # --- path safety -----------------------------------------------------

    def resolve(self, rel: str) -> Path:
        rel = (rel or ".").strip()
        if Path(rel).is_absolute():
            raise WorkspaceError(
                f"absolute paths are not allowed: {rel!r} — use a path relative to the workspace"
            )
        target = (self.root / rel).resolve()
        if target != self.root and self.root not in target.parents:
            raise WorkspaceError(f"{rel!r} is outside the workspace")
        return target

    def show(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.root)) or "."
        except ValueError:
            return str(p)

    # --- tools -----------------------------------------------------------

    def list_dir(self, path: str = ".") -> str:
        d = self.resolve(path)
        if not d.exists():
            raise WorkspaceError(f"no such directory: {self.show(d)}")
        if not d.is_dir():
            raise WorkspaceError(f"not a directory: {self.show(d)}")

        entries = sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        if not entries:
            return f"{self.show(d)}/ is empty"

        lines = []
        for e in entries[:MAX_LIST_ENTRIES]:
            if e.is_dir():
                lines.append(f"{e.name}/")
            else:
                try:
                    lines.append(f"{e.name}  ({e.stat().st_size} bytes)")
                except OSError:
                    lines.append(e.name)
        if len(entries) > MAX_LIST_ENTRIES:
            lines.append(f"… and {len(entries) - MAX_LIST_ENTRIES} more")
        return f"{self.show(d)}/\n" + "\n".join(lines)

    def read_file(self, path: str) -> str:
        f = self.resolve(path)
        if not f.exists():
            raise WorkspaceError(f"no such file: {self.show(f)}")
        if f.is_dir():
            raise WorkspaceError(f"{self.show(f)} is a directory")
        if f.stat().st_size > MAX_READ_BYTES:
            raise WorkspaceError(
                f"{self.show(f)} is {f.stat().st_size} bytes — too large to read"
            )
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise WorkspaceError(f"{self.show(f)} is not a text file")
        return text if text else "(file is empty)"

    def write_file(self, path: str, content: str = "") -> str:
        f = self.resolve(path)
        if f.is_dir():
            raise WorkspaceError(f"{self.show(f)} is a directory")
        existed = f.exists()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        verb = "Overwrote" if existed else "Created"
        return f"{verb} {self.show(f)} ({len(content)} bytes)"

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        f = self.resolve(path)
        if not f.exists():
            raise WorkspaceError(f"no such file: {self.show(f)}")
        text = f.read_text(encoding="utf-8")
        n = text.count(old_text)
        if n == 0:
            raise WorkspaceError(f"text not found in {self.show(f)}")
        if n > 1:
            raise WorkspaceError(
                f"text appears {n} times in {self.show(f)} — include more surrounding "
                f"context so the match is unique"
            )
        f.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {self.show(f)}"

    def make_dir(self, path: str) -> str:
        d = self.resolve(path)
        if d.exists():
            return f"{self.show(d)}/ already exists"
        d.mkdir(parents=True)
        return f"Created {self.show(d)}/"

    def delete_path(self, path: str, recursive: bool = False) -> str:
        p = self.resolve(path)
        if p == self.root:
            raise WorkspaceError("refusing to delete the workspace root")
        if not p.exists():
            raise WorkspaceError(f"no such path: {self.show(p)}")
        if p.is_dir():
            if any(p.iterdir()) and not recursive:
                raise WorkspaceError(
                    f"{self.show(p)}/ is not empty — pass recursive=true to delete it"
                )
            shutil.rmtree(p)
            return f"Deleted {self.show(p)}/"
        p.unlink()
        return f"Deleted {self.show(p)}"

    def move_path(self, src: str, dst: str) -> str:
        s, d = self.resolve(src), self.resolve(dst)
        if s == self.root:
            raise WorkspaceError("refusing to move the workspace root")
        if not s.exists():
            raise WorkspaceError(f"no such path: {self.show(s)}")
        if d.exists():
            raise WorkspaceError(f"{self.show(d)} already exists")
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return f"Moved {self.show(s)} → {self.show(d)}"

    # --- dispatch --------------------------------------------------------

    def run(self, name: str, args: dict) -> str:
        fn = {
            "list_dir": self.list_dir,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "edit_file": self.edit_file,
            "make_dir": self.make_dir,
            "delete_path": self.delete_path,
            "move_path": self.move_path,
        }.get(name)
        if fn is None:
            raise WorkspaceError(f"unknown tool: {name}")
        return fn(**args)

    def describe(self, name: str, args: dict) -> str:
        """One-line human summary, shown in the confirmation prompt."""
        if name == "write_file":
            f = self.root / args.get("path", "?")
            verb = "overwrite" if f.exists() else "create"
            return f"{verb} {args.get('path')} ({len(args.get('content', ''))} bytes)"
        if name == "edit_file":
            return f"edit {args.get('path')}"
        if name == "delete_path":
            rec = " recursively" if args.get("recursive") else ""
            return f"DELETE {args.get('path')}{rec}"
        if name == "move_path":
            return f"move {args.get('src')} → {args.get('dst')}"
        return f"{name} {args}"


# OpenAI-format tool schemas, sent to llama-server only when a workspace is active.
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List the contents of a directory in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path. Use '.' for the workspace root."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file's full contents.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Relative path to the file."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or overwrite an existing one with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file."},
                    "content": {"type": "string", "description": "Full contents to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace one exact snippet of text in a file. old_text must appear "
                "exactly once — include surrounding lines if needed to make it unique."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file."},
                    "old_text": {"type": "string", "description": "Exact text to replace."},
                    "new_text": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_dir",
            "description": "Create a directory, including any missing parents.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Relative path."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_path",
            "description": "Delete a file, or a directory (recursive=true for a non-empty one).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path."},
                    "recursive": {"type": "boolean", "description": "Required to delete a non-empty directory."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_path",
            "description": "Move or rename a file or directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "Existing relative path."},
                    "dst": {"type": "string", "description": "New relative path."},
                },
                "required": ["src", "dst"],
            },
        },
    },
]
