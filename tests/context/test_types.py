"""Tests for context extraction types."""

from bmad_assist.context.types import ExtractedContext, ImportBlock, Symbol


class TestSymbol:
    """Tests for Symbol dataclass."""

    def test_symbol_creation(self) -> None:
        sym = Symbol(
            name="authenticate",
            kind="function",
            start_line=10,
            end_line=20,
            body="def authenticate():\n    pass",
            signature="def authenticate():",
        )
        assert sym.name == "authenticate"
        assert sym.kind == "function"
        assert sym.start_line == 10
        assert sym.end_line == 20
        assert sym.is_modified is False

    def test_symbol_frozen(self) -> None:
        sym = Symbol(
            name="foo", kind="function", start_line=1, end_line=2,
            body="def foo(): pass", signature="def foo():",
        )
        try:
            sym.name = "bar"  # type: ignore[misc]
            assert False, "Should be frozen"
        except AttributeError:
            pass

    def test_symbol_modified(self) -> None:
        sym = Symbol(
            name="foo", kind="function", start_line=1, end_line=2,
            body="def foo(): pass", signature="def foo():",
            is_modified=True,
        )
        assert sym.is_modified is True


class TestImportBlock:
    """Tests for ImportBlock dataclass."""

    def test_import_block_creation(self) -> None:
        block = ImportBlock(content="import os\nimport sys", line_count=2)
        assert block.content == "import os\nimport sys"
        assert block.line_count == 2

    def test_empty_import_block(self) -> None:
        block = ImportBlock(content="", line_count=0)
        assert block.content == ""
        assert block.line_count == 0


class TestExtractedContext:
    """Tests for ExtractedContext dataclass."""

    def test_extracted_context_defaults(self) -> None:
        ctx = ExtractedContext(
            file_path="test.py",
            language="python",
            imports=ImportBlock(content="", line_count=0),
        )
        assert ctx.extraction_mode == "fallback"
        assert ctx.modified_symbols == []
        assert ctx.unmodified_symbols == []
        assert ctx.total_symbols == 0
        assert ctx.budget_used == 0
        assert ctx.budget_total == 4000

    def test_extracted_context_with_symbols(self) -> None:
        sym = Symbol(
            name="foo", kind="function", start_line=1, end_line=5,
            body="def foo(): pass", signature="def foo():",
            is_modified=True,
        )
        ctx = ExtractedContext(
            file_path="test.py",
            language="python",
            imports=ImportBlock(content="import os", line_count=1),
            modified_symbols=[sym],
            total_symbols=1,
            extraction_mode="diff_aware",
            budget_used=100,
            budget_total=4000,
        )
        assert ctx.extraction_mode == "diff_aware"
        assert len(ctx.modified_symbols) == 1
        assert ctx.modified_symbols[0].name == "foo"
