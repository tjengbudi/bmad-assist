"""Tests for Python parser (AST-based)."""

import pytest

from bmad_assist.context.parsers.python import parse_python_symbols


class TestPythonParserFunctions:
    """Tests for function extraction."""

    def test_simple_function(self) -> None:
        code = "def greet(name):\n    return f'Hello {name}'\n"
        imports, symbols = parse_python_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "greet"
        assert symbols[0].kind == "function"
        assert symbols[0].start_line == 1
        assert symbols[0].end_line == 2
        assert "def greet(name):" in symbols[0].body

    def test_async_function(self) -> None:
        code = "async def fetch(url):\n    return await get(url)\n"
        imports, symbols = parse_python_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "fetch"
        assert symbols[0].kind == "async_function"

    def test_multiple_functions(self) -> None:
        code = "def foo():\n    pass\n\ndef bar():\n    pass\n"
        _, symbols = parse_python_symbols(code)
        assert len(symbols) == 2
        assert symbols[0].name == "foo"
        assert symbols[1].name == "bar"

    def test_decorated_function(self) -> None:
        code = "@property\ndef value(self):\n    return self._val\n"
        _, symbols = parse_python_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "value"
        # Decorator should be included in body and start_line
        assert symbols[0].start_line == 1
        assert "@property" in symbols[0].body
        # But signature should be the def line
        assert "def value" in symbols[0].signature


class TestPythonParserClasses:
    """Tests for class and method extraction."""

    def test_class_with_methods(self) -> None:
        code = (
            "class AuthService:\n"
            "    def __init__(self):\n"
            "        pass\n"
            "\n"
            "    def authenticate(self, token):\n"
            "        return True\n"
        )
        _, symbols = parse_python_symbols(code)
        # Should have: class AuthService, method __init__, method authenticate
        names = [s.name for s in symbols]
        assert "AuthService" in names
        assert "AuthService.__init__" in names
        assert "AuthService.authenticate" in names

    def test_method_kind(self) -> None:
        code = "class Foo:\n    def bar(self):\n        pass\n"
        _, symbols = parse_python_symbols(code)
        methods = [s for s in symbols if s.kind == "method"]
        assert len(methods) == 1
        assert methods[0].name == "Foo.bar"

    def test_nested_class(self) -> None:
        code = (
            "class Outer:\n"
            "    class Inner:\n"
            "        def method(self):\n"
            "            pass\n"
        )
        _, symbols = parse_python_symbols(code)
        names = [s.name for s in symbols]
        assert "Outer" in names
        assert "Outer.Inner" in names


class TestPythonParserImports:
    """Tests for import extraction."""

    def test_simple_imports(self) -> None:
        code = "import os\nimport sys\n\ndef foo():\n    pass\n"
        imports, _ = parse_python_symbols(code)
        assert "import os" in imports.content
        assert "import sys" in imports.content
        assert imports.line_count == 2

    def test_from_imports(self) -> None:
        code = "from pathlib import Path\nfrom typing import Optional\n"
        imports, _ = parse_python_symbols(code)
        assert "from pathlib import Path" in imports.content
        assert imports.line_count == 2

    def test_no_imports(self) -> None:
        code = "def foo():\n    pass\n"
        imports, _ = parse_python_symbols(code)
        assert imports.content == ""
        assert imports.line_count == 0


class TestPythonParserEdgeCases:
    """Tests for edge cases."""

    def test_empty_file(self) -> None:
        imports, symbols = parse_python_symbols("")
        assert imports.content == ""
        assert symbols == []

    def test_only_imports(self) -> None:
        code = "import os\nimport sys\n"
        imports, symbols = parse_python_symbols(code)
        assert imports.line_count == 2
        assert symbols == []

    def test_syntax_error_raises_value_error(self) -> None:
        code = "def foo(\n    # missing closing paren\n"
        with pytest.raises(ValueError, match="syntax error"):
            parse_python_symbols(code)

    def test_large_file_raises_value_error(self) -> None:
        code = "x = 1\n" * 20000  # >100KB
        with pytest.raises(ValueError, match="exceeds"):
            parse_python_symbols(code)

    def test_signature_extraction(self) -> None:
        code = "def process(data: list[int], limit: int = 10) -> bool:\n    return True\n"
        _, symbols = parse_python_symbols(code)
        assert "def process(data: list[int], limit: int = 10) -> bool:" in symbols[0].signature
