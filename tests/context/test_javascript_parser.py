"""Tests for JavaScript/TypeScript parser (regex-based)."""

import pytest

from bmad_assist.context.parsers.javascript import parse_js_symbols


class TestJSFunctions:
    """Tests for function extraction."""

    def test_regular_function(self) -> None:
        code = "function greet(name) {\n    return 'Hello ' + name;\n}\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "greet"
        assert symbols[0].kind == "function"

    def test_async_function(self) -> None:
        code = "async function fetchData(url) {\n    const resp = await fetch(url);\n    return resp;\n}\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "fetchData"
        assert symbols[0].kind == "async_function"

    def test_arrow_function_with_braces(self) -> None:
        code = "const handler = async (req) => {\n    return req.body;\n};\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "handler"
        assert symbols[0].kind == "function"

    def test_arrow_function_without_braces(self) -> None:
        code = "const double = (x) => x * 2;\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "double"

    def test_export_default_function(self) -> None:
        code = "export default function main() {\n    console.log('hi');\n}\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "main"

    def test_export_function(self) -> None:
        code = "export function helper() {\n    return 42;\n}\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "helper"


class TestJSClasses:
    """Tests for class extraction."""

    def test_class_declaration(self) -> None:
        code = "class AuthService {\n    constructor() {}\n    login() {\n        return true;\n    }\n}\n"
        _, symbols = parse_js_symbols(code)
        classes = [s for s in symbols if s.kind == "class"]
        assert len(classes) == 1
        assert classes[0].name == "AuthService"

    def test_export_default_class(self) -> None:
        code = "export default class App {\n    render() {}\n}\n"
        _, symbols = parse_js_symbols(code)
        classes = [s for s in symbols if s.kind == "class"]
        assert len(classes) == 1
        assert classes[0].name == "App"


class TestJSImports:
    """Tests for import extraction."""

    def test_es6_import(self) -> None:
        code = "import { useState } from 'react';\n\nfunction App() {\n    return null;\n}\n"
        imports, _ = parse_js_symbols(code)
        assert "import" in imports.content
        assert imports.line_count >= 1

    def test_require_import(self) -> None:
        code = "const path = require('path');\n\nfunction main() {}\n"
        imports, _ = parse_js_symbols(code)
        assert "require" in imports.content

    def test_export_from(self) -> None:
        code = "export { default } from './utils';\n"
        imports, _ = parse_js_symbols(code)
        assert "export" in imports.content


class TestJSBraceCounting:
    """Tests for brace counting edge cases."""

    def test_braces_in_strings(self) -> None:
        code = 'function test() {\n    const s = "obj = {a: 1}";\n    return s;\n}\n'
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert "return s;" in symbols[0].body

    def test_braces_in_template_literal(self) -> None:
        code = "function format(obj) {\n    return `Value: ${obj.name}`;\n}\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert "return" in symbols[0].body

    def test_braces_in_comments(self) -> None:
        code = "function test() {\n    // { not a brace\n    /* { also not } */\n    return 1;\n}\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert "return 1;" in symbols[0].body

    def test_jsx_embedded_objects(self) -> None:
        code = "function Comp() {\n    return <div config={{x: 1}} />;\n}\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        # The function body should be complete
        assert symbols[0].body.count("{") >= 1


class TestJSTypeScript:
    """Tests for TypeScript-specific constructs."""

    def test_interface(self) -> None:
        code = "interface User {\n    name: string;\n    age: number;\n}\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "User"
        assert symbols[0].kind == "class"

    def test_enum(self) -> None:
        code = "enum Direction {\n    Up,\n    Down,\n    Left,\n    Right,\n}\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "Direction"
        assert symbols[0].kind == "class"

    def test_const_enum(self) -> None:
        code = "export const enum Status {\n    Active = 1,\n    Inactive = 0,\n}\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "Status"

    def test_type_alias(self) -> None:
        code = "type Result = string | number;\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "Result"

    def test_abstract_class(self) -> None:
        code = "abstract class Base {\n    abstract method(): void;\n}\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "Base"
        assert symbols[0].kind == "class"

    def test_nested_generics(self) -> None:
        code = "function merge<T extends Record<string, Array<number>>>(a: T): T {\n    return a;\n}\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "merge"

    def test_export_interface(self) -> None:
        code = "export interface Config {\n    port: number;\n}\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "Config"


class TestJSEdgeCases:
    """Tests for edge cases."""

    def test_large_file_raises(self) -> None:
        code = "const x = 1;\n" * 10000  # >100KB
        with pytest.raises(ValueError, match="exceeds"):
            parse_js_symbols(code)

    def test_empty_file(self) -> None:
        imports, symbols = parse_js_symbols("")
        assert symbols == []
        assert imports.content == ""

    def test_malformed_input_no_crash(self) -> None:
        code = "function { { { broken\n"
        # Should not raise — regex just won't match
        imports, symbols = parse_js_symbols(code)
        assert isinstance(symbols, list)

    def test_typescript_annotations(self) -> None:
        code = "function process<T>(data: T[]): Promise<void> {\n    return Promise.resolve();\n}\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "process"

    def test_single_param_arrow(self) -> None:
        code = "const double = x => x * 2;\n"
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].name == "double"

    def test_double_backslash_in_string(self) -> None:
        code = 'function test() {\n    const s = "C:\\\\";\n    return s;\n}\n'
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert "return s;" in symbols[0].body

    def test_multi_line_import(self) -> None:
        code = "import {\n    useState,\n    useEffect,\n} from 'react';\n\nfunction App() {}\n"
        imports, _ = parse_js_symbols(code)
        assert "useState" in imports.content
        assert "useEffect" in imports.content

    def test_import_source_ordering(self) -> None:
        code = "import 'reflect-metadata';\nimport { z } from 'zod';\nimport { a } from 'alpha';\n"
        imports, _ = parse_js_symbols(code)
        lines = imports.content.split("\n")
        # Should preserve source order, not alphabetical
        assert "reflect-metadata" in lines[0]
        assert "zod" in lines[1]
        assert "alpha" in lines[2]

    def test_braces_in_template_expr_string(self) -> None:
        code = 'function test() {\n    const x = `${obj["}"]}`;\n    return x;\n}\n'
        _, symbols = parse_js_symbols(code)
        assert len(symbols) == 1
        assert "return x;" in symbols[0].body
