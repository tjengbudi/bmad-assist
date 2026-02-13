"""Tests for Go parser (regex-based)."""

import pytest

from bmad_assist.context.parsers.go import parse_go_symbols


class TestGoFunctions:
    """Tests for function extraction."""

    def test_regular_function(self) -> None:
        code = 'package main\n\nfunc main() {\n\tfmt.Println("hello")\n}\n'
        _, symbols = parse_go_symbols(code)
        funcs = [s for s in symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].name == "main"

    def test_function_with_params(self) -> None:
        code = "package main\n\nfunc Add(a int, b int) int {\n\treturn a + b\n}\n"
        _, symbols = parse_go_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "Add"

    def test_multiple_functions(self) -> None:
        code = "package main\n\nfunc Foo() {\n}\n\nfunc Bar() {\n}\n"
        _, symbols = parse_go_symbols(code)
        names = [s.name for s in symbols]
        assert "Foo" in names
        assert "Bar" in names


class TestGoMethods:
    """Tests for method extraction."""

    def test_method_with_receiver(self) -> None:
        code = "func (s *Server) Handle(req Request) Response {\n\treturn Response{}\n}\n"
        _, symbols = parse_go_symbols(code)
        methods = [s for s in symbols if s.kind == "method"]
        assert len(methods) == 1
        assert methods[0].name == "Server.Handle"

    def test_value_receiver(self) -> None:
        code = "func (c Config) Validate() error {\n\treturn nil\n}\n"
        _, symbols = parse_go_symbols(code)
        methods = [s for s in symbols if s.kind == "method"]
        assert len(methods) == 1
        assert methods[0].name == "Config.Validate"


class TestGoTypes:
    """Tests for struct/interface extraction."""

    def test_struct(self) -> None:
        code = "type Server struct {\n\tport int\n\thost string\n}\n"
        _, symbols = parse_go_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "Server"
        assert symbols[0].kind == "class"

    def test_interface(self) -> None:
        code = "type Handler interface {\n\tServe(req Request) Response\n}\n"
        _, symbols = parse_go_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "Handler"
        assert symbols[0].kind == "class"


class TestGoImports:
    """Tests for import extraction."""

    def test_single_import(self) -> None:
        code = 'import "fmt"\n\nfunc main() {}\n'
        imports, _ = parse_go_symbols(code)
        assert '"fmt"' in imports.content

    def test_grouped_imports(self) -> None:
        code = 'import (\n\t"fmt"\n\t"os"\n)\n\nfunc main() {}\n'
        imports, _ = parse_go_symbols(code)
        assert '"fmt"' in imports.content
        assert '"os"' in imports.content


class TestGoBraceCounting:
    """Tests for brace counting edge cases."""

    def test_braces_in_strings(self) -> None:
        code = 'package main\n\nfunc test() {\n\ts := "{}"\n\treturn\n}\n'
        _, symbols = parse_go_symbols(code)
        assert len(symbols) == 1
        assert "return" in symbols[0].body

    def test_braces_in_comments(self) -> None:
        code = "package main\n\nfunc test() {\n\t// { not a brace\n\t/* { also not } */\n\treturn\n}\n"
        _, symbols = parse_go_symbols(code)
        assert len(symbols) == 1
        assert "return" in symbols[0].body

    def test_braces_in_rune_literals(self) -> None:
        code = "package main\n\nfunc isBrace(ch rune) bool {\n\treturn ch == '{' || ch == '}'\n}\n"
        _, symbols = parse_go_symbols(code)
        assert len(symbols) == 1
        assert "return" in symbols[0].body

    def test_double_backslash_in_string(self) -> None:
        code = 'package main\n\nfunc test() {\n\ts := "C:\\\\"\n\treturn\n}\n'
        _, symbols = parse_go_symbols(code)
        assert len(symbols) == 1
        assert "return" in symbols[0].body


class TestGoEdgeCases:
    """Tests for edge cases."""

    def test_large_file_raises(self) -> None:
        code = "var x = 1\n" * 12000  # >100KB
        with pytest.raises(ValueError, match="exceeds"):
            parse_go_symbols(code)

    def test_empty_file(self) -> None:
        imports, symbols = parse_go_symbols("")
        assert symbols == []
        assert imports.content == ""
