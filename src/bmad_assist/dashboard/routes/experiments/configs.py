"""Experiment config template route handlers.

Provides endpoints for listing and viewing config templates.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)


async def get_experiments_configs(request: Request) -> JSONResponse:
    """GET /api/experiments/configs - List config templates.

    Query parameters:
        sort_by: Sort field (name, run_count, last_run)
        sort_order: Sort direction (asc, desc)

    Returns:
        JSON response with configs array and total count.

    """
    from bmad_assist.dashboard.experiments import (
        ConfigSummary,
        discover_configs,
        discover_runs,
        get_config_run_stats,
    )
    from bmad_assist.experiments import load_config_template

    server = request.app.state.server
    experiments_dir = server.project_root / "experiments"

    # Parse and validate query params
    sort_by = request.query_params.get("sort_by", "name")
    sort_order = request.query_params.get("sort_order", "asc")

    valid_sort_fields = {"name", "run_count", "last_run"}
    if sort_by not in valid_sort_fields:
        return JSONResponse(
            {"error": "invalid_sort_by", "message": f"Invalid sort_by: {sort_by}"},
            status_code=400,
        )

    if sort_order not in {"asc", "desc"}:
        return JSONResponse(
            {"error": "invalid_sort_order", "message": f"Invalid sort_order: {sort_order}"},
            status_code=400,
        )

    try:
        # Discover configs (returns list of (name, path) tuples)
        config_tuples = await discover_configs(experiments_dir)
        if not config_tuples:
            return JSONResponse({"configs": [], "total": 0})

        # Load templates and build response
        summaries: list[ConfigSummary] = []

        for name, path in config_tuples:
            try:
                template = load_config_template(path)

                # Build providers dict
                providers = {}
                if template.providers is not None:
                    providers = {
                        "master": template.providers.master.model_dump(),
                        "multi": [p.model_dump() for p in template.providers.multi],
                    }

                summaries.append(
                    ConfigSummary(
                        name=name,
                        description=template.description,
                        source=str(path),
                        providers=providers,
                        run_count=0,  # Will be updated below
                        last_run=None,
                    )
                )
            except Exception as e:
                logger.warning("Failed to load config template %s: %s", name, e)
                continue

        # Get run statistics
        runs = await discover_runs(experiments_dir)
        config_names = [s.name for s in summaries]
        stats = get_config_run_stats(config_names, runs)

        # Update summaries with stats
        updated_summaries = []
        for summary in summaries:
            config_stats = stats.get(summary.name)
            updated_summaries.append(
                ConfigSummary(
                    name=summary.name,
                    description=summary.description,
                    source=summary.source,
                    providers=summary.providers,
                    run_count=config_stats.run_count if config_stats else 0,
                    last_run=config_stats.last_run if config_stats else None,
                )
            )
        summaries = updated_summaries

        # Apply sorting
        min_datetime = datetime.min.replace(tzinfo=UTC)
        sort_key_funcs: dict[str, Any] = {
            "name": lambda s: s.name.lower(),
            "run_count": lambda s: s.run_count,
            "last_run": lambda s: s.last_run or min_datetime,
        }
        summaries.sort(key=sort_key_funcs[sort_by], reverse=(sort_order == "desc"))

        return JSONResponse(
            {
                "configs": [s.model_dump(mode="json") for s in summaries],
                "total": len(summaries),
            }
        )

    except Exception:
        logger.exception("Failed to list configs")
        return JSONResponse(
            {"error": "server_error", "message": "Internal server error"},
            status_code=500,
        )


async def get_experiment_config(request: Request) -> JSONResponse:
    """GET /api/experiments/configs/{config_name} - Get config details.

    Path parameters:
        config_name: The config template identifier

    Returns:
        JSON response with full config details or 404.

    """
    from bmad_assist.dashboard.experiments import (
        ConfigDetails,
        discover_configs,
        discover_runs,
        get_config_run_stats,
        get_yaml_content,
        validate_run_id,
    )
    from bmad_assist.experiments import load_config_template

    server = request.app.state.server
    config_name = request.path_params["config_name"]

    # Validate config_name format
    if not validate_run_id(config_name):
        return JSONResponse(
            {"error": "bad_request", "message": f"Invalid config_name format: {config_name}"},
            status_code=400,
        )

    experiments_dir = server.project_root / "experiments"

    try:
        # Discover configs
        config_tuples = await discover_configs(experiments_dir)

        # Find config by name
        config_path = None
        for name, path in config_tuples:
            if name == config_name:
                config_path = path
                break

        if config_path is None:
            return JSONResponse(
                {"error": "not_found", "message": f"Config not found: {config_name}"},
                status_code=404,
            )

        # Load template
        try:
            template = load_config_template(config_path)
        except Exception as e:
            return JSONResponse(
                {"error": "server_error", "message": f"Failed to load config: {e}"},
                status_code=500,
            )

        # Get run statistics
        runs = await discover_runs(experiments_dir)
        stats = get_config_run_stats([config_name], runs)
        config_stats = stats.get(config_name)

        # Get YAML content
        yaml_content = await get_yaml_content(str(config_path))

        # Build providers dict
        providers = {}
        if template.providers is not None:
            providers = {
                "master": template.providers.master.model_dump(),
                "multi": [p.model_dump() for p in template.providers.multi],
            }

        # Build response
        details = ConfigDetails(
            name=config_name,
            description=template.description,
            source=str(config_path),
            providers=providers,
            yaml_content=yaml_content,
            run_count=config_stats.run_count if config_stats else 0,
            last_run=config_stats.last_run if config_stats else None,
            recent_runs=config_stats.recent_runs if config_stats else [],
        )

        return JSONResponse(details.model_dump(mode="json"))

    except Exception:
        logger.exception("Failed to get config %s", config_name)
        return JSONResponse(
            {"error": "server_error", "message": "Internal server error"},
            status_code=500,
        )


routes = [
    Route("/api/experiments/configs", get_experiments_configs, methods=["GET"]),
    Route("/api/experiments/configs/{config_name}", get_experiment_config, methods=["GET"]),
]
