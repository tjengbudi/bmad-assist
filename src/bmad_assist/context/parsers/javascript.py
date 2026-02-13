"""JavaScript/TypeScript symbol extraction using regex + brace counting.

Extracts functions, async functions, classes, arrow functions, interfaces,
enums, and type aliases from JS/TS source code. Uses a string-aware state
machine for brace counting to avoid counting braces inside strings,
template literals, and comments.
"""

from __future__ import annotations

import logging
import re
from enum import Enum, auto

from bmad_assist.context.types import ImportBlock, Symbol

logger = logging.getLogger(__name__)

# Maximum file size for parsing (100KB)
MAX_PARSE_SIZE = 100 * 1024

# Import patterns
_IMPORT_PATTERNS = [
    # Multi-line destructured import: import { X, Y, Z } from '...';
    re.compile(r"^import\s+\{[^}]*\}\s+from\s+['\"].*?['\"];?\s*$", re.MULTILINE),
    # Single-line: import X from '...'; import { X } from '...';
    re.compile(r"^import\s+.+?\s+from\s+['\"].*?['\"];?\s*$", re.MULTILINE),
    # Side-effect import: import '...';
    re.compile(r"^import\s+['\"].*?['\"];?\s*$", re.MULTILINE),
    # CommonJS require
    re.compile(
        r"^(?:const|let|var)\s+.+?\s*=\s*require\s*\(.+?\);?\s*$", re.MULTILINE
    ),
    # Re-export: export { X } from '...';
    re.compile(r"^export\s+.+?\s+from\s+['\"].*?['\"];?\s*$", re.MULTILINE),
]

# Symbol declaration patterns — matches the start of a symbol
_SYMBOL_PATTERNS = [
    # async function name( or async function name<T>(
    re.compile(
        r"^(?:export\s+(?:default\s+)?)?async\s+function\s+(\w+)\s*(?:<[^(]*>)?\s*\(",
        re.MULTILINE,
    ),
    # function name( or function name<T>(
    re.compile(
        r"^(?:export\s+(?:default\s+)?)?function\s+(\w+)\s*(?:<[^(]*>)?\s*\(",
        re.MULTILINE,
    ),
    # abstract class Name or class Name
    re.compile(
        r"^(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+(\w+)",
        re.MULTILINE,
    ),
    # TypeScript: interface Name
    re.compile(
        r"^(?:export\s+(?:default\s+)?)?interface\s+(\w+)",
        re.MULTILINE,
    ),
    # TypeScript: enum Name or const enum Name
    re.compile(
        r"^(?:export\s+(?:default\s+)?)?(?:const\s+)?enum\s+(\w+)",
        re.MULTILINE,
    ),
    # TypeScript: type Name<T> = ...
    re.compile(
        r"^(?:export\s+)?type\s+(\w+)\s*(?:<[^(]*>)?\s*=",
        re.MULTILINE,
    ),
    # const/let/var name = (...) => { or const/let/var name = param =>
    re.compile(
        r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|\w+)\s*(?::\s*[^=>\n]+)?\s*=>",
        re.MULTILINE,
    ),
]


class _State(Enum):
    """Parser state for brace counting."""

    CODE = auto()
    STRING_SINGLE = auto()
    STRING_DOUBLE = auto()
    TEMPLATE = auto()
    TEMPLATE_EXPR = auto()
    LINE_COMMENT = auto()
    BLOCK_COMMENT = auto()


def parse_js_symbols(content: str) -> tuple[ImportBlock, list[Symbol]]:
    """Extract symbols and imports from JavaScript/TypeScript source.

    Args:
        content: JS/TS source code.

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
    """Extract import statements from JS/TS source.

    Preserves source ordering and deduplicates.

    Args:
        content: Source code.

    Returns:
        ImportBlock with all import lines.

    """
    seen: set[str] = set()
    import_entries: list[tuple[int, str]] = []

    for pattern in _IMPORT_PATTERNS:
        for match in pattern.finditer(content):
            text = match.group(0)
            if text not in seen:
                seen.add(text)
                import_entries.append((match.start(), text))

    # Sort by position in source (preserves original order)
    import_entries.sort(key=lambda x: x[0])
    lines = [text for _, text in import_entries]
    text = "\n".join(lines)
    return ImportBlock(content=text, line_count=len(lines))


def _extract_symbols(content: str) -> list[Symbol]:
    """Extract function/class symbols from JS/TS source.

    Args:
        content: Source code.

    Returns:
        List of Symbol dataclasses.

    """
    lines = content.split("\n")
    symbols: list[Symbol] = []
    used_ranges: set[int] = set()  # Track which start positions are used

    for pattern in _SYMBOL_PATTERNS:
        for match in pattern.finditer(content):
            start_pos = match.start()

            # Skip if this position was already captured by a previous pattern
            if start_pos in used_ranges:
                continue

            name = match.group(1)
            start_line = content[:start_pos].count("\n") + 1

            # Determine kind
            match_text = match.group(0)
            if "class " in match_text or "interface " in match_text or "enum " in match_text:
                kind = "class"
            elif "async " in match_text and "function " in match_text:
                kind = "async_function"
            elif "=>" in match_text:
                kind = "function"
            else:
                kind = "function"

            # Find the opening brace
            brace_search_start = match.end()
            brace_pos = _find_opening_brace(content, brace_search_start)

            if brace_pos is not None:
                # Use brace counting to find end
                end_pos = _find_matching_brace(content, brace_pos)
                if end_pos is not None:
                    end_line = content[:end_pos + 1].count("\n") + 1
                    body = content[start_pos:end_pos + 1]
                else:
                    # Couldn't find matching brace — take to end of file
                    end_line = len(lines)
                    body = content[start_pos:]
            else:
                # Arrow function without braces or type alias: single expression
                # Find end of line or semicolon
                eol = content.find("\n", match.end())
                if eol == -1:
                    eol = len(content)
                end_line = start_line
                body = content[start_pos:eol]

            sig_line = lines[start_line - 1] if start_line <= len(lines) else ""
            signature = sig_line.rstrip()

            used_ranges.add(start_pos)
            symbols.append(
                Symbol(
                    name=name,
                    kind=kind,  # type: ignore[arg-type]
                    start_line=start_line,
                    end_line=end_line,
                    body=body,
                    signature=signature,
                )
            )

    # Sort by start_line
    symbols.sort(key=lambda s: s.start_line)
    return symbols


def _find_opening_brace(content: str, start: int) -> int | None:
    """Find the first structural opening brace after start position.

    Skips over type annotations, arrow tokens, whitespace, strings,
    and comments.

    Args:
        content: Source code.
        start: Position to start searching from.

    Returns:
        Position of opening brace, or None if not found within 500 chars.

    """
    limit = min(start + 500, len(content))
    state = _State.CODE
    skip_next = False

    for i in range(start, limit):
        if skip_next:
            skip_next = False
            continue

        ch = content[i]

        if state == _State.CODE:
            if ch == "{":
                return i
            if (ch == ";" or ch == "\n") and i > start + 100:
                # Likely an arrow without braces
                return None
            if ch == "/" and i + 1 < limit:
                if content[i + 1] == "/":
                    state = _State.LINE_COMMENT
                    continue
                if content[i + 1] == "*":
                    state = _State.BLOCK_COMMENT
                    continue
            if ch == "'":
                state = _State.STRING_SINGLE
            elif ch == '"':
                state = _State.STRING_DOUBLE
            elif ch == "`":
                state = _State.TEMPLATE

        elif state == _State.STRING_SINGLE:
            if ch == "\\":
                skip_next = True
            elif ch == "'":
                state = _State.CODE

        elif state == _State.STRING_DOUBLE:
            if ch == "\\":
                skip_next = True
            elif ch == '"':
                state = _State.CODE

        elif state == _State.TEMPLATE:
            if ch == "\\":
                skip_next = True
            elif ch == "`":
                state = _State.CODE

        elif state == _State.LINE_COMMENT:
            if ch == "\n":
                state = _State.CODE

        elif state == _State.BLOCK_COMMENT:
            if ch == "*" and i + 1 < limit and content[i + 1] == "/":
                state = _State.CODE
                skip_next = True  # Skip the '/'

    return None


def _find_matching_brace(content: str, brace_pos: int) -> int | None:
    """Find the closing brace matching the one at brace_pos.

    Uses string-aware state machine to skip braces inside strings,
    template literals, and comments. Escape handling uses forward-skip
    (not prev-char check) to correctly handle consecutive escapes.

    Args:
        content: Source code.
        brace_pos: Position of the opening brace.

    Returns:
        Position of matching closing brace, or None.

    """
    depth = 0
    state = _State.CODE
    template_expr_depth = 0
    i = brace_pos
    length = len(content)

    while i < length:
        ch = content[i]

        if state == _State.CODE:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
            elif ch == "'":
                state = _State.STRING_SINGLE
            elif ch == '"':
                state = _State.STRING_DOUBLE
            elif ch == "`":
                state = _State.TEMPLATE
            elif ch == "/" and i + 1 < length:
                if content[i + 1] == "/":
                    state = _State.LINE_COMMENT
                    i += 1
                elif content[i + 1] == "*":
                    state = _State.BLOCK_COMMENT
                    i += 1

        elif state == _State.STRING_SINGLE:
            if ch == "\\":
                i += 1  # Skip escaped char
            elif ch == "'":
                state = _State.CODE

        elif state == _State.STRING_DOUBLE:
            if ch == "\\":
                i += 1  # Skip escaped char
            elif ch == '"':
                state = _State.CODE

        elif state == _State.TEMPLATE:
            if ch == "\\":
                i += 1  # Skip escaped char
            elif ch == "`":
                state = _State.CODE
            elif ch == "$" and i + 1 < length and content[i + 1] == "{":
                state = _State.TEMPLATE_EXPR
                template_expr_depth = 1
                i += 1  # Skip the {

        elif state == _State.TEMPLATE_EXPR:
            if ch == "\\":
                i += 1  # Skip escaped char
            elif ch == "'":
                # Skip single-quoted string inside template expression
                i += 1
                while i < length:
                    if content[i] == "\\":
                        i += 1
                    elif content[i] == "'":
                        break
                    i += 1
            elif ch == '"':
                # Skip double-quoted string inside template expression
                i += 1
                while i < length:
                    if content[i] == "\\":
                        i += 1
                    elif content[i] == '"':
                        break
                    i += 1
            elif ch == "{":
                template_expr_depth += 1
            elif ch == "}":
                template_expr_depth -= 1
                if template_expr_depth == 0:
                    state = _State.TEMPLATE

        elif state == _State.LINE_COMMENT:
            if ch == "\n":
                state = _State.CODE

        elif state == _State.BLOCK_COMMENT and ch == "*" and i + 1 < length and content[i + 1] == "/":
            state = _State.CODE
            i += 1  # Skip the /

        i += 1

    return None
