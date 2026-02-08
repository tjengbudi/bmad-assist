"""Bundled workflow templates for bmad-assist."""

import logging
import sys
from importlib.resources import files
from pathlib import Path

logger = logging.getLogger(__name__)

# Python 3.14+ moved Traversable to importlib.resources.abc
if sys.version_info >= (3, 14):
    from importlib.resources.abc import Traversable
else:
    from importlib.abc import Traversable


def get_bundled_workflow_dir(workflow_name: str) -> Path | None:
    """Get path to bundled workflow directory.

    Args:
        workflow_name: Workflow name (e.g., 'dev-story', 'create-story').

    Returns:
        Path to workflow directory, or None if not bundled.

    Note:
        importlib.resources.files() returns Traversable, not Path.
        We validate with Traversable methods, then convert to Path.

    """
    try:
        # Get package resources path (returns Traversable)
        package_path: Traversable = files("bmad_assist.workflows")
        workflow_path: Traversable = package_path / workflow_name

        # Validate using Traversable methods
        if not workflow_path.is_dir():
            return None

        workflow_yaml = workflow_path / "workflow.yaml"
        if not workflow_yaml.is_file():
            return None

        # Convert Traversable to Path for return
        # str(Traversable) gives the filesystem path
        return Path(str(workflow_path))
    except Exception:
        return None


def get_bundled_cache(workflow_name: str) -> tuple[str, str] | None:
    """Get bundled pre-compiled template and metadata content.

    Reads content via Traversable.read_text() to support zip/wheel installs
    where Traversable → Path conversion would fail.

    Args:
        workflow_name: Workflow name (e.g., 'create-story').

    Returns:
        Tuple of (tpl_content, meta_content) as strings if both files exist,
        None otherwise.

    """
    try:
        package_path: Traversable = files("bmad_assist.workflows")
        cache_dir: Traversable = package_path / "cache"

        tpl_path: Traversable = cache_dir / f"{workflow_name}.tpl.xml"
        meta_path: Traversable = cache_dir / f"{workflow_name}.tpl.xml.meta.yaml"

        if not tpl_path.is_file() or not meta_path.is_file():
            return None

        tpl_content = tpl_path.read_text(encoding="utf-8")
        meta_content = meta_path.read_text(encoding="utf-8")
        return (tpl_content, meta_content)
    except Exception:
        logger.debug("Failed to load bundled cache for %s", workflow_name, exc_info=True)
        return None


def list_bundled_cache() -> list[str]:
    """List all bundled pre-compiled cache workflow names.

    Returns:
        List of workflow names that have bundled tpl.xml + meta.yaml.

    """
    try:
        package_path: Traversable = files("bmad_assist.workflows")
        cache_dir: Traversable = package_path / "cache"

        if not cache_dir.is_dir():
            return []

        workflows = []
        for item in cache_dir.iterdir():
            if item.name.endswith(".tpl.xml") and item.is_file():
                wf_name = item.name.removesuffix(".tpl.xml")
                meta = cache_dir / f"{wf_name}.tpl.xml.meta.yaml"
                if meta.is_file():
                    workflows.append(wf_name)
        return sorted(workflows)
    except Exception:
        logger.debug("Failed to list bundled cache", exc_info=True)
        return []


def list_bundled_workflows() -> list[str]:
    """List all bundled workflow names.

    Returns:
        List of workflow directory names that contain workflow.yaml.

    """
    try:
        package_path: Traversable = files("bmad_assist.workflows")
        workflows = []
        for item in package_path.iterdir():
            # item is Traversable, use its methods
            if item.is_dir() and (item / "workflow.yaml").is_file():
                workflows.append(item.name)
        return sorted(workflows)
    except Exception:
        return []
