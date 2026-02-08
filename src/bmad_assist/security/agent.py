"""Security agent runner.

Invokes a single LLM provider with a pre-compiled security review prompt,
parses structured output into SecurityReport. Does NOT save to cache —
the orchestrator handles cache persistence post-gather.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from bmad_assist.core.exceptions import BmadAssistError, ProviderError, ProviderTimeoutError
from bmad_assist.security.report import SecurityFinding, SecurityReport

if TYPE_CHECKING:
    from bmad_assist.core.config import Config
    from bmad_assist.providers.base import BaseProvider

logger = logging.getLogger(__name__)

# Markers for structured security report output
SECURITY_REPORT_START = "<!-- SECURITY_REPORT_START -->"
SECURITY_REPORT_END = "<!-- SECURITY_REPORT_END -->"


async def run_security_review(
    config: Config,
    project_path: Path,
    compiled_prompt: str,
    timeout: int,
    languages: list[str] | None = None,
    patterns_loaded: int = 0,
) -> SecurityReport:
    """Run security review using LLM provider.

    Resolves provider from config (security_agent.provider_config or master fallback),
    invokes with compiled prompt, parses structured output.

    Args:
        config: Application configuration.
        project_path: Project root directory.
        compiled_prompt: Pre-compiled security review prompt.
        timeout: Timeout in seconds.
        languages: Detected languages (for report metadata).
        patterns_loaded: Number of CWE patterns loaded.

    Returns:
        SecurityReport with findings. Returns empty report on failure.

    """
    start_time = time.perf_counter()

    try:
        # Resolve provider
        provider, model, settings_file, thinking, reasoning_effort = _resolve_provider(config)

        logger.info(
            "Starting security review (provider=%s, model=%s, timeout=%ds)",
            provider.provider_name,
            model or "default",
            timeout,
        )

        # Invoke provider (synchronous call wrapped in asyncio)
        import asyncio

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: provider.invoke(
                compiled_prompt,
                model=model,
                timeout=timeout,
                settings_file=settings_file,
                cwd=project_path,
                disable_tools=True,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
            ),
        )

        duration = time.perf_counter() - start_time

        if not result.stdout:
            logger.warning("Security review returned empty output")
            return SecurityReport(
                languages_detected=languages or [],
                patterns_loaded=patterns_loaded,
                scan_duration_seconds=duration,
                analysis_quality="degraded",
            )

        # Parse structured output
        findings = _parse_security_output(result.stdout)

        report = SecurityReport(
            findings=findings,
            languages_detected=languages or [],
            patterns_loaded=patterns_loaded,
            scan_duration_seconds=duration,
            analysis_quality="full",
        )

        logger.info(
            "Security review complete: %d findings (%.1fs)",
            len(findings),
            duration,
        )

        return report

    except ProviderTimeoutError:
        duration = time.perf_counter() - start_time
        logger.warning("Security review timed out after %.1fs", duration)
        return SecurityReport(
            languages_detected=languages or [],
            patterns_loaded=patterns_loaded,
            scan_duration_seconds=duration,
            timed_out=True,
            analysis_quality="failed",
        )

    except (ProviderError, BmadAssistError) as e:
        duration = time.perf_counter() - start_time
        logger.warning("Security review failed (non-blocking): %s", e)
        return SecurityReport(
            languages_detected=languages or [],
            patterns_loaded=patterns_loaded,
            scan_duration_seconds=duration,
            analysis_quality="failed",
        )

    except (RuntimeError, OSError, ValueError, TypeError, AttributeError) as e:
        duration = time.perf_counter() - start_time
        logger.warning(
            "Security review unexpected error (non-blocking): %s",
            e,
            exc_info=True,
        )
        return SecurityReport(
            languages_detected=languages or [],
            patterns_loaded=patterns_loaded,
            scan_duration_seconds=duration,
            analysis_quality="failed",
        )


def _resolve_provider(
    config: Config,
) -> tuple[BaseProvider, str, Path | None, bool | None, str | None]:
    """Resolve provider from security_agent config or master fallback.

    Returns:
        Tuple of (provider_instance, model, settings_file, thinking, reasoning_effort).

    """
    from bmad_assist.providers import get_provider

    sec_config = config.security_agent
    if sec_config.provider_config is not None:
        pc = sec_config.provider_config
        provider = get_provider(pc.provider)
        return (
            provider,
            pc.model,
            pc.settings_path,
            pc.thinking if pc.thinking else None,
            pc.reasoning_effort,
        )

    # Fallback to master provider
    master = config.providers.master
    provider = get_provider(master.provider)
    return (
        provider,
        master.model,
        master.settings_path,
        None,  # thinking
        None,  # reasoning_effort
    )


def _parse_security_output(output: str) -> list[SecurityFinding]:
    """Parse structured security report from LLM output.

    Extracts JSON between SECURITY_REPORT_START/END markers.
    Falls back to trying to find JSON block in output.

    Args:
        output: Raw LLM output string.

    Returns:
        List of SecurityFinding objects.

    """
    # Primary: extract between markers
    start_idx = output.find(SECURITY_REPORT_START)
    end_idx = output.find(SECURITY_REPORT_END)

    if start_idx != -1 and end_idx != -1:
        json_str = output[start_idx + len(SECURITY_REPORT_START):end_idx].strip()
        return _parse_findings_json(json_str)

    # Fallback: find JSON block in output (```json ... ```)
    json_match = re.search(r"```json\s*\n(.*?)\n```", output, re.DOTALL)
    if json_match:
        return _parse_findings_json(json_match.group(1))

    # Last resort: try to find raw JSON object
    json_match = re.search(r'\{\s*"findings"\s*:', output, re.DOTALL)
    if json_match:
        # Find matching closing brace
        brace_start = json_match.start()
        depth = 0
        for i in range(brace_start, len(output)):
            if output[i] == "{":
                depth += 1
            elif output[i] == "}":
                depth -= 1
                if depth == 0:
                    return _parse_findings_json(output[brace_start:i + 1])

    logger.warning("Could not extract structured security report from output")
    return []


def _parse_findings_json(json_str: str) -> list[SecurityFinding]:
    """Parse findings from JSON string.

    Args:
        json_str: JSON string with findings array.

    Returns:
        List of SecurityFinding objects.

    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse security findings JSON: %s", e)
        return []

    findings_data = data.get("findings", []) if isinstance(data, dict) else data
    if not isinstance(findings_data, list):
        logger.warning("Expected findings array, got %s", type(findings_data))
        return []

    findings: list[SecurityFinding] = []
    for idx, f in enumerate(findings_data):
        if not isinstance(f, dict):
            continue
        try:
            finding = SecurityFinding(
                id=f.get("id", f"SEC-{idx + 1:03d}"),
                file_path=str(f.get("file_path") or f.get("file") or "unknown"),
                line_number=int(f.get("line_number") or f.get("line") or 0),
                cwe_id=str(f.get("cwe_id") or f.get("cwe") or "CWE-unknown"),
                severity=str(f.get("severity") or "MEDIUM").upper(),
                title=f.get("title", "Untitled finding"),
                description=f.get("description", ""),
                remediation=str(f.get("remediation") or f.get("fix") or ""),
                confidence=_normalize_confidence(f.get("confidence", 0.5)),
            )
            findings.append(finding)
        except (ValueError, TypeError) as e:
            logger.warning("Skipping malformed finding at index %d: %s", idx, e)

    return findings


def _normalize_confidence(value: object) -> float:
    """Normalize confidence to 0.0-1.0 range.

    LLMs may return confidence as percentage (0-100) or fraction (0.0-1.0).

    """
    try:
        conf = float(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return 0.5
    if conf > 1.0:
        conf = conf / 100.0
    return max(0.0, min(1.0, conf))
