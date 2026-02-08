"""A/B Workflow Tester for experiment framework.

Provides infrastructure for running controlled A/B tests comparing
different workflow configurations, patch-sets, or prompts against
the same fixture and story set.
"""

from bmad_assist.experiments.ab.config import (
    ABTestConfig,
    ABVariantConfig,
    load_ab_test_config,
)
from bmad_assist.experiments.ab.runner import (
    ABTestResult,
    ABTestRunner,
    ABVariantResult,
)
from bmad_assist.experiments.ab.worktree import (
    WorktreeInfo,
    cleanup_ab_worktrees,
    create_ab_worktrees,
    create_worktree,
    remove_worktree,
    validate_fixture_is_git_repo,
    validate_ref_exists,
)

__all__ = [
    "ABTestConfig",
    "ABVariantConfig",
    "load_ab_test_config",
    "ABTestRunner",
    "ABTestResult",
    "ABVariantResult",
    "WorktreeInfo",
    "create_worktree",
    "remove_worktree",
    "create_ab_worktrees",
    "cleanup_ab_worktrees",
    "validate_fixture_is_git_repo",
    "validate_ref_exists",
]
