"""Tests for deep_verify/file_filter.py — DVFileFilter."""

from __future__ import annotations

import pytest

from bmad_assist.deep_verify.file_filter import DVFileFilter


class TestDVFileFilterCommon:
    """Common exclusions (apply to all stacks)."""

    @pytest.fixture()
    def filt(self) -> DVFileFilter:
        return DVFileFilter(stacks=[])

    @pytest.mark.parametrize(
        "path",
        [
            "README.md",
            "docs/guide.txt",
            "CHANGELOG.rst",
            "data/export.csv",
            "logo.png",
            "font.woff2",
            "archive.zip",
            "report.pdf",
            "lib.so",
            "app.db",
            "bundle.js.map",
        ],
    )
    def test_excludes_common_extensions(self, filt: DVFileFilter, path: str) -> None:
        assert filt.should_exclude(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            ".git/config",
            "node_modules/lodash/index.js",
            "dist/bundle.js",
            "build/output.js",
            ".idea/workspace.xml",
            "tests/test_auth.py",
            "test/unit/test_foo.py",
            "__tests__/Button.test.tsx",
            "fixtures/sample.json",
        ],
    )
    def test_excludes_common_directories(self, filt: DVFileFilter, path: str) -> None:
        assert filt.should_exclude(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "LICENSE",
            "Dockerfile",
            "docker-compose.yml",
            ".gitignore",
            "Makefile",
        ],
    )
    def test_excludes_common_filenames(self, filt: DVFileFilter, path: str) -> None:
        assert filt.should_exclude(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            ".env",
            ".env.local",
            ".env.production",
            "yarn.lock",
            "poetry.lock",
        ],
    )
    def test_excludes_common_patterns(self, filt: DVFileFilter, path: str) -> None:
        assert filt.should_exclude(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "utils/test_helpers.py",  # test_ in dir name, caught by pattern
            "src/conftest.py",
            "lib/auth_test.py",
            "src/component.spec.ts",
            "src/component.test.js",
        ],
    )
    def test_excludes_test_files(self, filt: DVFileFilter, path: str) -> None:
        assert filt.should_exclude(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "src/auth.py",
            "src/components/Button.tsx",
            "lib/utils.go",
            "cmd/main.go",
            "src/main.rs",
        ],
    )
    def test_keeps_source_files(self, filt: DVFileFilter, path: str) -> None:
        assert filt.should_exclude(path) is False


class TestDVFileFilterJavaScript:
    """JavaScript/TypeScript stack exclusions."""

    @pytest.fixture()
    def filt(self) -> DVFileFilter:
        return DVFileFilter(stacks=["javascript"])

    @pytest.mark.parametrize(
        "path",
        [
            "package.json",
            "tsconfig.json",
            "src/data.json",
            "config/settings.json",
        ],
    )
    def test_excludes_json(self, filt: DVFileFilter, path: str) -> None:
        assert filt.should_exclude(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "vite.config.ts",
            "webpack.config.js",
            "next.config.mjs",
            "eslint.config.cjs",
        ],
    )
    def test_excludes_config_files(self, filt: DVFileFilter, path: str) -> None:
        assert filt.should_exclude(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "src/types/api.d.ts",
            "lib/global.d.mts",
        ],
    )
    def test_excludes_declaration_files(self, filt: DVFileFilter, path: str) -> None:
        assert filt.should_exclude(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "src/styles/main.css",
            "src/styles/app.scss",
        ],
    )
    def test_excludes_stylesheets(self, filt: DVFileFilter, path: str) -> None:
        assert filt.should_exclude(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            ".next/static/chunks/main.js",
            ".nuxt/dist/server.js",
            "storybook-static/index.html",
        ],
    )
    def test_excludes_framework_dirs(self, filt: DVFileFilter, path: str) -> None:
        assert filt.should_exclude(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            ".eslintrc.json",
            ".prettierrc.yaml",
            "jest.config.ts",
            "vitest.config.ts",
            "tailwind.config.js",
            "postcss.config.js",
            "babel.config.js",
        ],
    )
    def test_excludes_tool_configs(self, filt: DVFileFilter, path: str) -> None:
        assert filt.should_exclude(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "src/App.tsx",
            "src/utils/helpers.ts",
            "src/api/auth.ts",
            "lib/core.js",
        ],
    )
    def test_keeps_source_files(self, filt: DVFileFilter, path: str) -> None:
        assert filt.should_exclude(path) is False


class TestDVFileFilterPython:
    """Python stack exclusions."""

    @pytest.fixture()
    def filt(self) -> DVFileFilter:
        return DVFileFilter(stacks=["python"])

    @pytest.mark.parametrize(
        "path",
        [
            "setup.py",
            "pyproject.toml",
            "tox.ini",
            ".flake8",
            "src/cache.pyc",
        ],
    )
    def test_excludes_python_configs(self, filt: DVFileFilter, path: str) -> None:
        assert filt.should_exclude(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "__pycache__/module.cpython-311.pyc",
            ".mypy_cache/report.json",
            ".venv/lib/site.py",
            "htmlcov/index.html",
        ],
    )
    def test_excludes_python_dirs(self, filt: DVFileFilter, path: str) -> None:
        assert filt.should_exclude(path) is True

    def test_excludes_migration_files(self, filt: DVFileFilter) -> None:
        assert filt.should_exclude("app/migrations/0001_initial.py") is True

    def test_keeps_source_files(self, filt: DVFileFilter) -> None:
        assert filt.should_exclude("src/bmad_assist/cli.py") is False
        assert filt.should_exclude("src/core/engine.py") is False


class TestDVFileFilterMultiStack:
    """Multiple stacks combined."""

    def test_combined_exclusions(self) -> None:
        filt = DVFileFilter(stacks=["python", "javascript"])
        # Python-specific
        assert filt.should_exclude("setup.py") is True
        # JS-specific
        assert filt.should_exclude("package.json") is True
        # Source files still pass
        assert filt.should_exclude("src/auth.py") is False
        assert filt.should_exclude("src/App.tsx") is False

    def test_unknown_stack_ignored(self) -> None:
        filt = DVFileFilter(stacks=["brainfuck"])
        # Only common rules apply
        assert filt.should_exclude("README.md") is True
        assert filt.should_exclude("src/auth.py") is False


class TestDVFileFilterForProject:
    """Test for_project class method."""

    def test_for_project_with_python(self, tmp_path: type) -> None:
        """Detect Python stack from pyproject.toml marker."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        filt = DVFileFilter.for_project(tmp_path)
        assert filt.should_exclude("setup.py") is True

    def test_for_project_with_js(self, tmp_path: type) -> None:
        """Detect JS stack from package.json marker."""
        (tmp_path / "package.json").write_text('{"name": "test"}')
        filt = DVFileFilter.for_project(tmp_path)
        assert filt.should_exclude("vite.config.ts") is True

    def test_for_project_empty(self, tmp_path: type) -> None:
        """No markers → only common rules."""
        filt = DVFileFilter.for_project(tmp_path)
        assert filt.should_exclude("README.md") is True
        assert filt.should_exclude("src/auth.py") is False
