"""Go symbol extraction using regex + brace counting.

Extracts functions, methods, structs, and interfaces from Go source.
Go's strict formatting (gofmt) makes regex reliable.
"""

from __future__ import annotations

import logging
import re

from bmad_assist.context.types import ImportBlock, Symbol

logger = logging.getLogger(__name__)

# Maximum file size for parsing (100KB)
MAX_PARSE_SIZE = 100 * 1024

# Import patterns
_SINGLE_IMPORT = re.compile(r'^import\s+"[^"]*"\s*$', re.MULTILINE)
_GROUPED_IMPORT = re.compile(r"^import\s*\(", re.MULTILINE)

# Symbol patterns
_FUNC_PATTERN = re.compile(
    r"^func\s+(\w+)\s*\(", re.MULTILINE
)
_METHOD_PATTERN = re.compile(
    r"^func\s+\(\s*\w+\s+\*?(\w+)\s*\)\s+(\w+)\s*\(", re.MULTILINE
)
_STRUCT_PATTERN = re.compile(
    r"^type\s+(\w+)\s+struct\s*\{", re.MULTILINE
)
_INTERFACE_PATTERN = re.compile(
    r"^type\s+(\w+)\s+interface\s*\{", re.MULTILINE
)

# Maximum distance to search for opening brace (avoids body-less func issues)
_BRACE_SEARCH_LIMIT = 500


def parse_go_symbols(content: str) -> tuple[ImportBlock, list[Symbol]]:
    """Extract symbols and imports from Go source.

    Args:
        content: Go source code.

    Returns:
        Tuple of (ImportBlock, list[Symbol]).

    Raises:
        ValueError: If content exceeds MAX_PARSE_SIZE.

    """
    if len(content.encode("utf-8")) > MAX_PARSE_SIZE:
        raise ValueError(f"File exceeds {MAX_PARSE_SIZE} bytes, skipping parse")

    import_block = _extract_imports(content)
    symbols = _extract_symbols(content)

    return import_block, symbols


def _extract_imports(content: str) -> ImportBlock:
    """Extract import statements from Go source.

    Args:
        content: Source code.

    Returns:
        ImportBlock with all import lines.

    """
    import_texts: list[str] = []

    # Single imports
    for match in _SINGLE_IMPORT.finditer(content):
        import_texts.append(match.group(0))

    # Grouped imports — find matching closing paren
    for match in _GROUPED_IMPORT.finditer(content):
        start = match.start()
        paren_pos = content.find("(", match.end() - 1)
        if paren_pos == -1:
            continue
        close_pos = content.find(")", paren_pos + 1)
        if close_pos == -1:
            continue
        import_texts.append(content[start : close_pos + 1])

    text = "\n".join(import_texts)
    line_count = text.count("\n") + 1 if text else 0
    return ImportBlock(content=text, line_count=line_count)


def _extract_symbols(content: str) -> list[Symbol]:
    """Extract function/method/struct/interface symbols from Go source.

    Args:
        content: Source code.

    Returns:
        List of Symbol dataclasses.

    """
    lines = content.split("\n")
    symbols: list[Symbol] = []
    used_positions: set[int] = set()

    # Methods (func (r *Receiver) Name()  — must match before plain func)
    for match in _METHOD_PATTERN.finditer(content):
        start_pos = match.start()
        receiver = match.group(1)
        name = match.group(2)
        start_line = content[:start_pos].count("\n") + 1

        search_limit = min(match.end() + _BRACE_SEARCH_LIMIT, len(content))
        brace_pos = content.find("{", match.end(), search_limit)
        if brace_pos == -1:
            continue

        end_pos = _find_matching_brace(content, brace_pos)
        if end_pos is not None:
            end_line = content[:end_pos + 1].count("\n") + 1
            body = content[start_pos:end_pos + 1]
        else:
            end_line = len(lines)
            body = content[start_pos:]

        sig = lines[start_line - 1].rstrip() if start_line <= len(lines) else ""
        used_positions.add(start_pos)
        symbols.append(
            Symbol(
                name=f"{receiver}.{name}",
                kind="method",
                start_line=start_line,
                end_line=end_line,
                body=body,
                signature=sig,
            )
        )

    # Plain functions
    for match in _FUNC_PATTERN.finditer(content):
        start_pos = match.start()
        if start_pos in used_positions:
            continue

        name = match.group(1)
        start_line = content[:start_pos].count("\n") + 1

        search_limit = min(match.end() + _BRACE_SEARCH_LIMIT, len(content))
        brace_pos = content.find("{", match.end(), search_limit)
        if brace_pos == -1:
            continue

        end_pos = _find_matching_brace(content, brace_pos)
        if end_pos is not None:
            end_line = content[:end_pos + 1].count("\n") + 1
            body = content[start_pos:end_pos + 1]
        else:
            end_line = len(lines)
            body = content[start_pos:]

        sig = lines[start_line - 1].rstrip() if start_line <= len(lines) else ""
        used_positions.add(start_pos)
        symbols.append(
            Symbol(
                name=name,
                kind="function",
                start_line=start_line,
                end_line=end_line,
                body=body,
                signature=sig,
            )
        )

    # Structs
    for match in _STRUCT_PATTERN.finditer(content):
        start_pos = match.start()
        name = match.group(1)
        start_line = content[:start_pos].count("\n") + 1

        brace_pos = content.find("{", match.end() - 1)
        if brace_pos == -1:
            continue

        end_pos = _find_matching_brace(content, brace_pos)
        if end_pos is not None:
            end_line = content[:end_pos + 1].count("\n") + 1
            body = content[start_pos:end_pos + 1]
        else:
            end_line = len(lines)
            body = content[start_pos:]

        sig = lines[start_line - 1].rstrip() if start_line <= len(lines) else ""
        symbols.append(
            Symbol(
                name=name,
                kind="class",
                start_line=start_line,
                end_line=end_line,
                body=body,
                signature=sig,
            )
        )

    # Interfaces
    for match in _INTERFACE_PATTERN.finditer(content):
        start_pos = match.start()
        name = match.group(1)
        start_line = content[:start_pos].count("\n") + 1

        brace_pos = content.find("{", match.end() - 1)
        if brace_pos == -1:
            continue

        end_pos = _find_matching_brace(content, brace_pos)
        if end_pos is not None:
            end_line = content[:end_pos + 1].count("\n") + 1
            body = content[start_pos:end_pos + 1]
        else:
            end_line = len(lines)
            body = content[start_pos:]

        sig = lines[start_line - 1].rstrip() if start_line <= len(lines) else ""
        symbols.append(
            Symbol(
                name=name,
                kind="class",
                start_line=start_line,
                end_line=end_line,
                body=body,
                signature=sig,
            )
        )

    symbols.sort(key=lambda s: s.start_line)
    return symbols


def _find_matching_brace(content: str, brace_pos: int) -> int | None:
    """Find matching closing brace using state machine.

    Handles strings, raw strings, rune literals, line comments, and block
    comments to avoid counting braces inside non-code contexts. Escape
    handling uses forward-skip to correctly handle consecutive escapes.

    Args:
        content: Source code.
        brace_pos: Position of the opening brace.

    Returns:
        Position of matching closing brace, or None.

    """
    depth = 0
    i = brace_pos
    length = len(content)
    # States: code, string, raw_string, rune, line_comment, block_comment
    state = "code"

    while i < length:
        ch = content[i]

        if state == "code":
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
            elif ch == '"':
                state = "string"
            elif ch == "`":
                state = "raw_string"
            elif ch == "'":
                state = "rune"
            elif ch == "/" and i + 1 < length:
                if content[i + 1] == "/":
                    state = "line_comment"
                    i += 1
                elif content[i + 1] == "*":
                    state = "block_comment"
                    i += 1

        elif state == "string":
            if ch == "\\":
                i += 1  # Skip escaped char
            elif ch == '"':
                state = "code"

        elif state == "raw_string":
            if ch == "`":
                state = "code"

        elif state == "rune":
            if ch == "\\":
                i += 1  # Skip escaped char
            elif ch == "'":
                state = "code"

        elif state == "line_comment":
            if ch == "\n":
                state = "code"

        elif state == "block_comment" and ch == "*" and i + 1 < length and content[i + 1] == "/":
                state = "code"
                i += 1  # Skip the /

        i += 1

    return None
