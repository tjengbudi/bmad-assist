"""Python symbol extraction using AST.

Extracts functions, async functions, classes, and methods from Python
source code using the stdlib ast module. Respects MAX_PARSE_SIZE to
avoid parsing huge generated files.
"""

from __future__ import annotations

import ast
import logging

from bmad_assist.context.types import ImportBlock, Symbol

logger = logging.getLogger(__name__)

# Maximum file size for AST parsing (100KB)
MAX_PARSE_SIZE = 100 * 1024


def parse_python_symbols(content: str) -> tuple[ImportBlock, list[Symbol]]:
    """Extract symbols and imports from Python source.

    Args:
        content: Python source code.

    Returns:
        Tuple of (ImportBlock, list[Symbol]).

    Raises:
        ValueError: If content exceeds MAX_PARSE_SIZE or has syntax errors.

    """
    if len(content.encode("utf-8")) > MAX_PARSE_SIZE:
        raise ValueError(f"File exceeds {MAX_PARSE_SIZE} bytes, skipping AST parse")

    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        raise ValueError(f"Python syntax error: {e}") from e

    lines = content.splitlines(keepends=True)

    # Extract imports
    import_block = _extract_imports(tree, lines)

    # Extract symbols
    symbols = _extract_symbols(tree, lines)

    return import_block, symbols


def _extract_imports(tree: ast.Module, lines: list[str]) -> ImportBlock:
    """Extract import statements from AST.

    Args:
        tree: Parsed AST module.
        lines: Source lines with line endings.

    Returns:
        ImportBlock with all import lines.

    """
    import_line_numbers: set[int] = set()

    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Import, ast.ImportFrom))
            and hasattr(node, "lineno")
            and hasattr(node, "end_lineno")
        ):
            start = node.lineno
            end = node.end_lineno or start
            for line_num in range(start, end + 1):
                if line_num <= len(lines):
                    import_line_numbers.add(line_num)

    # Sort by line number for consistent output
    sorted_lines = [
        lines[ln - 1]
        for ln in sorted(import_line_numbers)
        if ln <= len(lines)
    ]

    content = "".join(sorted_lines).rstrip("\n")
    return ImportBlock(content=content, line_count=len(sorted_lines))


def _extract_symbols(
    tree: ast.Module | list[ast.stmt],
    lines: list[str],
    class_name: str | None = None,
) -> list[Symbol]:
    """Extract function/class/method symbols from AST.

    Args:
        tree: AST node to walk (Module or ClassDef body).
        lines: Source lines with line endings.
        class_name: If extracting class methods, the class name for qualification.

    Returns:
        List of Symbol dataclasses.

    """
    symbols: list[Symbol] = []

    # Walk only top-level children for proper nesting
    children = tree.body if isinstance(tree, ast.Module) else tree

    for node in children:
        if isinstance(node, ast.ClassDef):
            # Include decorators in range for diff-aware matching
            start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            end = node.end_lineno or node.lineno
            body = "".join(lines[start - 1 : end])
            sig = lines[node.lineno - 1].rstrip("\n") if node.lineno <= len(lines) else ""

            name = f"{class_name}.{node.name}" if class_name else node.name
            symbols.append(
                Symbol(
                    name=name,
                    kind="class",
                    start_line=start,
                    end_line=end,
                    body=body,
                    signature=sig,
                )
            )

            # Extract methods from class body
            method_symbols = _extract_symbols(node.body, lines, class_name=node.name)
            symbols.extend(method_symbols)

        elif isinstance(node, ast.AsyncFunctionDef):
            start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            end = node.end_lineno or node.lineno
            body = "".join(lines[start - 1 : end])
            sig = lines[node.lineno - 1].rstrip("\n") if node.lineno <= len(lines) else ""

            name = f"{class_name}.{node.name}" if class_name else node.name
            if class_name:
                symbols.append(Symbol(name=name, kind="method", start_line=start, end_line=end, body=body, signature=sig))
            else:
                symbols.append(Symbol(name=name, kind="async_function", start_line=start, end_line=end, body=body, signature=sig))

        elif isinstance(node, ast.FunctionDef):
            start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            end = node.end_lineno or node.lineno
            body = "".join(lines[start - 1 : end])
            sig = lines[node.lineno - 1].rstrip("\n") if node.lineno <= len(lines) else ""

            name = f"{class_name}.{node.name}" if class_name else node.name
            if class_name:
                symbols.append(Symbol(name=name, kind="method", start_line=start, end_line=end, body=body, signature=sig))
            else:
                symbols.append(Symbol(name=name, kind="function",
                    start_line=start,
                    end_line=end,
                    body=body,
                    signature=sig,
                )
            )

    return symbols
