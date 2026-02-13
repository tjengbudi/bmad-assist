"""Tests for context formatters."""

from bmad_assist.context.formatter import format_for_dv, format_for_source_context
from bmad_assist.context.types import ExtractedContext, ImportBlock, Symbol


class TestFormatForDV:
    """Tests for Deep Verify formatter."""

    def test_dv_format_header(self) -> None:
        ctx = ExtractedContext(
            file_path="src/auth.py",
            language="python",
            imports=ImportBlock(content="import os", line_count=1),
            total_symbols=3,
            extraction_mode="diff_aware",
            budget_used=100,
            budget_total=4000,
        )
        result = format_for_dv(ctx)
        assert "# File: src/auth.py" in result
        assert "3 symbols" in result

    def test_dv_format_modified_labels(self) -> None:
        sym = Symbol(
            name="authenticate",
            kind="function",
            start_line=10,
            end_line=20,
            body="def authenticate():\n    pass",
            signature="def authenticate():",
            is_modified=True,
        )
        ctx = ExtractedContext(
            file_path="src/auth.py",
            language="python",
            imports=ImportBlock(content="", line_count=0),
            modified_symbols=[sym],
            total_symbols=1,
            extraction_mode="diff_aware",
            budget_used=100,
            budget_total=4000,
        )
        result = format_for_dv(ctx)
        assert "Modified" in result
        assert "authenticate" in result
        assert "lines 10-20" in result

    def test_dv_format_signatures(self) -> None:
        sym = Symbol(
            name="helper",
            kind="function",
            start_line=5,
            end_line=8,
            body="def helper():\n    pass",
            signature="def helper():",
        )
        ctx = ExtractedContext(
            file_path="src/utils.py",
            language="python",
            imports=ImportBlock(content="", line_count=0),
            unmodified_symbols=[sym],
            total_symbols=1,
            extraction_mode="no_diff",
            budget_used=50,
            budget_total=4000,
        )
        result = format_for_dv(ctx)
        assert "Signatures" in result
        assert "def helper():" in result

    def test_dv_fallback_mode(self) -> None:
        ctx = ExtractedContext(
            file_path="test.rb",
            language="unknown",
            imports=ImportBlock(content="", line_count=0),
            extraction_mode="fallback",
            budget_used=100,
            budget_total=4000,
        )
        result = format_for_dv(ctx)
        assert "Fallback" in result


class TestFormatForSourceContext:
    """Tests for source_context formatter."""

    def test_source_context_no_labels(self) -> None:
        sym = Symbol(
            name="foo",
            kind="function",
            start_line=1,
            end_line=3,
            body="def foo():\n    return 1",
            signature="def foo():",
        )
        ctx = ExtractedContext(
            file_path="test.py",
            language="python",
            imports=ImportBlock(content="import os", line_count=1),
            unmodified_symbols=[sym],
            total_symbols=1,
            extraction_mode="no_diff",
            budget_used=50,
            budget_total=4000,
        )
        result = format_for_source_context(ctx, 4000)
        assert "import os" in result
        assert "def foo():" in result
        # No DV-specific labels
        assert "Modified" not in result
        assert "Signatures" not in result

    def test_source_context_budget_respected(self) -> None:
        sym = Symbol(
            name="big_func",
            kind="function",
            start_line=1,
            end_line=100,
            body="def big_func():\n" + "    x = 1\n" * 200,
            signature="def big_func():",
        )
        ctx = ExtractedContext(
            file_path="test.py",
            language="python",
            imports=ImportBlock(content="", line_count=0),
            unmodified_symbols=[sym],
            total_symbols=1,
            extraction_mode="no_diff",
            budget_used=1000,
            budget_total=500,
        )
        result = format_for_source_context(ctx, 500)
        assert len(result) <= 500

    def test_source_context_fallback_returns_empty(self) -> None:
        ctx = ExtractedContext(
            file_path="test.rb",
            language="unknown",
            imports=ImportBlock(content="", line_count=0),
            extraction_mode="fallback",
            budget_used=0,
            budget_total=4000,
        )
        result = format_for_source_context(ctx, 4000)
        assert result == ""
