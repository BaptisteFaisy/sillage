"""Size guard: files < 500 lines, functions < 50 lines (user rule 2026-10-24).

Scans every Python file of the repository. Exits non-zero on the first
violation summary so CI (`ci` job) fails. Pure standard library.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

MAX_FILE_LINES = 500
MAX_FUNCTION_LINES = 50
SKIP_DIRS = {".git", ".github", "__pycache__", ".venv", "node_modules", "build", "dist", ".mypy_cache"}


def python_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        found.append(path)
    return found


def function_lengths(source: str, path: Path) -> list[tuple[str, int]]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # a syntax error is reported by ruff, not here
        print(f"  ! cannot parse {path}: {exc}")
        return []
    lengths: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            last = max(
                (getattr(child, "end_lineno", None) or node.lineno for child in ast.walk(node)),
                default=node.lineno,
            )
            lengths.append((node.name, last - node.lineno + 1))
    return lengths


def scan(root: Path) -> list[str]:
    problems: list[str] = []
    for path in python_files(root):
        source = path.read_text(encoding="utf-8", errors="replace")
        lines = source.count("\n") + (0 if source.endswith("\n") else 1)
        if lines > MAX_FILE_LINES:
            problems.append(f"{path}: {lines} lines (> {MAX_FILE_LINES})")
        for name, length in function_lengths(source, path):
            if length > MAX_FUNCTION_LINES:
                problems.append(f"{path}:{name}: {length} lines (> {MAX_FUNCTION_LINES})")
    return problems


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    problems = scan(root)
    if problems:
        print(f"LIMITS: {len(problems)} violation(s)")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    total = len(python_files(root))
    print(
        f"LIMITS OK — {total} python file(s), max {MAX_FILE_LINES} lines / {MAX_FUNCTION_LINES} per function"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
