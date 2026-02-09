"""Tests for the test command group.

Tests for bmad-assist test scorecard and bmad-assist test compare commands.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from bmad_assist.cli import app
from bmad_assist.commands.test import _validate_fixture

runner = CliRunner()


class TestValidateFixture:
    """Tests for _validate_fixture helper."""

    def test_validate_fixture_not_found(self, tmp_path: Path) -> None:
        """Test that missing fixture raises typer.Exit."""
        import typer

        with pytest.raises(typer.Exit) as exc_info:
            _validate_fixture(tmp_path, "nonexistent-fixture")

        assert exc_info.value.exit_code == 1

    def test_validate_fixture_is_file(self, tmp_path: Path) -> None:
        """Test that fixture as file (not directory) raises typer.Exit."""
        import typer

        # Create fixtures dir with a file instead of directory
        fixtures_dir = tmp_path / "experiments" / "fixtures"
        fixtures_dir.mkdir(parents=True)
        (fixtures_dir / "not-a-dir").write_text("I am a file")

        with pytest.raises(typer.Exit) as exc_info:
            _validate_fixture(tmp_path, "not-a-dir")

        assert exc_info.value.exit_code == 1

    def test_validate_fixture_exists(self, tmp_path: Path) -> None:
        """Test that valid fixture directory returns path."""
        fixtures_dir = tmp_path / "experiments" / "fixtures"
        fixture_path = fixtures_dir / "valid-fixture"
        fixture_path.mkdir(parents=True)

        result = _validate_fixture(tmp_path, "valid-fixture")

        assert result == fixture_path
        assert result.exists()
        assert result.is_dir()


class TestScorecardCommand:
    """Tests for bmad-assist test scorecard command."""

    def test_help_output(self) -> None:
        """Test that scorecard --help shows expected content."""
        result = runner.invoke(app, ["test", "scorecard", "--help"])

        assert result.exit_code == 0
        assert "scorecard" in result.output.lower()
        assert "fixture" in result.output.lower()
        assert "--output" in result.output or "-o" in result.output
        assert "--verbose" in result.output or "-v" in result.output

    def test_scorecard_fixture_not_found(self, tmp_path: Path) -> None:
        """Test error when fixture doesn't exist."""
        result = runner.invoke(
            app,
            ["test", "scorecard", "nonexistent", "--project", str(tmp_path)],
        )

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    @pytest.mark.skipif(
        not (Path("experiments/fixtures").exists() and list(Path("experiments/fixtures").iterdir())),
        reason="No fixtures available in experiments/fixtures/",
    )
    def test_scorecard_integration(self) -> None:
        """Integration test with real fixture (skipped if no fixtures)."""
        fixtures = list(Path("experiments/fixtures").iterdir())
        if not fixtures:
            pytest.skip("No fixtures available")

        fixture_name = fixtures[0].name

        result = runner.invoke(
            app,
            ["test", "scorecard", fixture_name, "--project", "."],
        )

        # Should either succeed or fail gracefully
        assert result.exit_code in (0, 1)


class TestCompareCommand:
    """Tests for bmad-assist test compare command."""

    def test_help_output(self) -> None:
        """Test that compare --help shows expected content."""
        result = runner.invoke(app, ["test", "compare", "--help"])

        assert result.exit_code == 0
        assert "compare" in result.output.lower()
        assert "fixture1" in result.output.lower() or "FIXTURE1" in result.output
        assert "fixture2" in result.output.lower() or "FIXTURE2" in result.output

    def test_compare_first_fixture_not_found(self, tmp_path: Path) -> None:
        """Test error when first fixture doesn't exist."""
        result = runner.invoke(
            app,
            ["test", "compare", "nonexistent1", "nonexistent2", "--project", str(tmp_path)],
        )

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_compare_second_fixture_not_found(self, tmp_path: Path) -> None:
        """Test error when second fixture doesn't exist."""
        # Create only first fixture
        fixtures_dir = tmp_path / "experiments" / "fixtures"
        (fixtures_dir / "exists").mkdir(parents=True)

        result = runner.invoke(
            app,
            ["test", "compare", "exists", "nonexistent", "--project", str(tmp_path)],
        )

        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestTestAppHelp:
    """Tests for bmad-assist test --help."""

    def test_test_group_help(self) -> None:
        """Test that test --help shows both subcommands."""
        result = runner.invoke(app, ["test", "--help"])

        assert result.exit_code == 0
        assert "scorecard" in result.output
        assert "compare" in result.output
        assert "testing framework" in result.output.lower()


class TestScorecardSubprocessMock:
    """Tests for scorecard command with mocked subprocess."""

    def test_scorecard_command_construction(self, tmp_path: Path) -> None:
        """Test that scorecard builds correct subprocess command."""
        # Setup fixture path only (no scorecard script needed with -m invocation)
        fixtures_dir = tmp_path / "experiments" / "fixtures"
        (fixtures_dir / "test-fixture").mkdir(parents=True)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Grade: A (90%)"
        mock_result.stderr = ""

        with patch("bmad_assist.commands.test.subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(
                app,
                ["test", "scorecard", "test-fixture", "--project", str(tmp_path)],
            )

            assert result.exit_code == 0
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            # Verify -m invocation with module path
            assert cmd[1] == "-m"
            assert cmd[2] == "bmad_assist.experiments.testing.scorecard"
            assert cmd[3] == "test-fixture"
            # Verify cwd is set correctly
            assert call_args[1]["cwd"] == tmp_path

    def test_scorecard_timeout_handling(self, tmp_path: Path) -> None:
        """Test that scorecard handles timeout correctly."""
        import subprocess

        fixtures_dir = tmp_path / "experiments" / "fixtures"
        (fixtures_dir / "test-fixture").mkdir(parents=True)

        with patch(
            "bmad_assist.commands.test.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="test", timeout=300),
        ):
            result = runner.invoke(
                app,
                ["test", "scorecard", "test-fixture", "--project", str(tmp_path)],
            )

            assert result.exit_code == 1
            assert "timed out" in result.output.lower()


class TestCompareSubprocessMock:
    """Tests for compare command with mocked subprocess."""

    def test_compare_command_construction(self, tmp_path: Path) -> None:
        """Test that compare builds correct subprocess command."""
        fixtures_dir = tmp_path / "experiments" / "fixtures"
        (fixtures_dir / "fixture1").mkdir(parents=True)
        (fixtures_dir / "fixture2").mkdir(parents=True)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Comparison complete"
        mock_result.stderr = ""

        with patch("bmad_assist.commands.test.subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(
                app,
                ["test", "compare", "fixture1", "fixture2", "--project", str(tmp_path)],
            )

            assert result.exit_code == 0
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            # Verify -m invocation with module path
            assert cmd[1] == "-m"
            assert cmd[2] == "bmad_assist.experiments.testing.scorecard"
            # Verify command includes --compare flag
            assert "--compare" in cmd
            assert "fixture1" in cmd
            assert "fixture2" in cmd
