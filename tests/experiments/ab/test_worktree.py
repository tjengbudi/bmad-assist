"""Tests for git worktree management.

Tests cover:
- WorktreeInfo dataclass
- validate_fixture_is_git_repo()
- validate_ref_exists()
- create_worktree() / remove_worktree()
- create_ab_worktrees() / cleanup_ab_worktrees()
- Error handling for non-git repos, invalid refs, existing paths
"""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from bmad_assist.core.exceptions import IsolationError
from bmad_assist.experiments.ab.worktree import (
    WorktreeInfo,
    cleanup_ab_worktrees,
    create_ab_worktrees,
    create_worktree,
    remove_worktree,
    validate_fixture_is_git_repo,
    validate_ref_exists,
)


@pytest.fixture
def git_fixture(tmp_path: Path) -> Path:
    """Create a real git repo fixture for worktree tests."""
    import subprocess

    repo = tmp_path / "fixture-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    # Create an initial commit so HEAD exists
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    return repo


class TestWorktreeInfo:
    """Tests for WorktreeInfo dataclass."""

    def test_create(self, tmp_path: Path) -> None:
        """Create a WorktreeInfo with all fields."""
        info = WorktreeInfo(path=tmp_path, ref="abc123", variant_label="a")
        assert info.path == tmp_path
        assert info.ref == "abc123"
        assert info.variant_label == "a"

    def test_frozen(self, tmp_path: Path) -> None:
        """Frozen dataclass rejects attribute mutation."""
        info = WorktreeInfo(path=tmp_path, ref="abc123", variant_label="a")
        with pytest.raises(FrozenInstanceError):
            info.ref = "new"  # type: ignore[misc]


class TestValidateFixtureIsGitRepo:
    """Tests for validate_fixture_is_git_repo."""

    def test_valid_git_repo(self, git_fixture: Path) -> None:
        """Valid git repo passes validation."""
        validate_fixture_is_git_repo(git_fixture)  # should not raise

    def test_non_git_directory(self, tmp_path: Path) -> None:
        """Non-git directory raises IsolationError."""
        with pytest.raises(IsolationError, match="not a git repository"):
            validate_fixture_is_git_repo(tmp_path)


class TestValidateRefExists:
    """Tests for validate_ref_exists."""

    def test_valid_ref_head(self, git_fixture: Path) -> None:
        """HEAD ref passes validation in a repo with commits."""
        validate_ref_exists(git_fixture, "HEAD")  # should not raise

    def test_invalid_ref(self, git_fixture: Path) -> None:
        """Nonexistent ref raises IsolationError."""
        with pytest.raises(IsolationError, match="not found"):
            validate_ref_exists(git_fixture, "nonexistent-ref-abc123")


class TestCreateWorktree:
    """Tests for create_worktree."""

    def test_create_success(self, git_fixture: Path, tmp_path: Path) -> None:
        """Successfully create a detached worktree."""
        wt_path = tmp_path / "worktree-a"
        info = create_worktree(git_fixture, wt_path, "HEAD", "variant-a")
        assert info.path == wt_path.resolve()
        assert info.ref == "HEAD"
        assert info.variant_label == "variant-a"
        assert wt_path.exists()

    def test_existing_path_raises(self, git_fixture: Path, tmp_path: Path) -> None:
        """Existing target path raises IsolationError."""
        wt_path = tmp_path / "existing"
        wt_path.mkdir()
        with pytest.raises(IsolationError, match="already exists"):
            create_worktree(git_fixture, wt_path, "HEAD", "variant-a")

    def test_invalid_ref_raises(self, git_fixture: Path, tmp_path: Path) -> None:
        """Invalid ref raises IsolationError."""
        wt_path = tmp_path / "worktree-bad"
        with pytest.raises(IsolationError):
            create_worktree(git_fixture, wt_path, "nonexistent-ref-xyz", "bad")


class TestRemoveWorktree:
    """Tests for remove_worktree."""

    def test_remove_existing(self, git_fixture: Path, tmp_path: Path) -> None:
        """Remove an existing worktree cleanly."""
        wt_path = tmp_path / "wt-remove"
        create_worktree(git_fixture, wt_path, "HEAD", "remove-test")
        assert wt_path.exists()
        remove_worktree(git_fixture, wt_path)
        assert not wt_path.exists()

    def test_remove_nonexistent_noop(self, git_fixture: Path, tmp_path: Path) -> None:
        """Removing a nonexistent worktree is a safe no-op."""
        remove_worktree(git_fixture, tmp_path / "does-not-exist")


class TestCreateABWorktrees:
    """Tests for create_ab_worktrees."""

    def test_creates_both(self, git_fixture: Path, tmp_path: Path) -> None:
        """Create both variant worktrees successfully."""
        base = tmp_path / "ab-base"
        wt_a, wt_b = create_ab_worktrees(git_fixture, base, "HEAD", "test")
        assert wt_a.path.exists()
        assert wt_b.path.exists()
        assert wt_a.variant_label == "variant-a"
        assert wt_b.variant_label == "variant-b"
        # Cleanup
        remove_worktree(git_fixture, wt_a.path)
        remove_worktree(git_fixture, wt_b.path)

    def test_non_git_fixture_raises(self, tmp_path: Path) -> None:
        """Non-git fixture directory raises IsolationError."""
        base = tmp_path / "ab-base"
        with pytest.raises(IsolationError, match="not a git repository"):
            create_ab_worktrees(tmp_path, base, "HEAD", "test")

    def test_invalid_ref_raises(self, git_fixture: Path, tmp_path: Path) -> None:
        """Invalid ref raises IsolationError."""
        base = tmp_path / "ab-bad-ref"
        with pytest.raises(IsolationError, match="not found"):
            create_ab_worktrees(git_fixture, base, "bad-ref-xyz", "test")

    def test_cleanup_a_if_b_fails(self, git_fixture: Path, tmp_path: Path) -> None:
        """If worktree B creation fails, worktree A is cleaned up."""
        base = tmp_path / "ab-partial"
        # Pre-create worktree-b path to force B creation to fail
        (base / "worktree-b").mkdir(parents=True)

        with pytest.raises(IsolationError, match="already exists"):
            create_ab_worktrees(git_fixture, base, "HEAD", "test")

        # worktree-a should have been cleaned up
        assert not (base / "worktree-a").exists()


class TestCleanupABWorktrees:
    """Tests for cleanup_ab_worktrees."""

    def test_cleanup_both(self, git_fixture: Path, tmp_path: Path) -> None:
        """Clean up both worktrees and the base directory."""
        base = tmp_path / "ab-cleanup"
        wt_a, wt_b = create_ab_worktrees(git_fixture, base, "HEAD", "test")
        cleanup_ab_worktrees(git_fixture, wt_a, wt_b, base)
        assert not wt_a.path.exists()
        assert not wt_b.path.exists()
        assert not base.exists()

    def test_cleanup_none_safe(self, git_fixture: Path) -> None:
        """Handle None worktrees without error."""
        cleanup_ab_worktrees(git_fixture, None, None)

    def test_cleanup_partial(self, git_fixture: Path, tmp_path: Path) -> None:
        """Handle only one worktree being set (no base_dir removal)."""
        base = tmp_path / "ab-partial-cleanup"
        wt_a, wt_b = create_ab_worktrees(git_fixture, base, "HEAD", "test")
        # Cleanup only A (no base_dir removal so B survives)
        cleanup_ab_worktrees(git_fixture, wt_a, None)
        assert not wt_a.path.exists()
        assert wt_b.path.exists()
        # Clean up B manually
        remove_worktree(git_fixture, wt_b.path)
