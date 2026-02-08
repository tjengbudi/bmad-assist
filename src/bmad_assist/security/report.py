"""Security report models and serialization.

Provides SecurityFinding and SecurityReport Pydantic models with
confidence filtering for synthesis prompt inclusion.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SecurityFinding(BaseModel):
    """A single security finding from the security agent.

    Attributes:
        id: Unique finding identifier (e.g., "SEC-001").
        file_path: Path to the affected file.
        line_number: Line number of the finding (0 if unknown).
        cwe_id: CWE identifier (e.g., "CWE-89").
        severity: Severity level: HIGH, MEDIUM, or LOW.
        title: Short finding title.
        description: Detailed finding description.
        remediation: Suggested fix.
        confidence: Confidence score (0.0-1.0).

    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Unique finding identifier")
    file_path: str = Field(description="Path to affected file")
    line_number: int = Field(default=0, description="Line number (0 if unknown)")
    cwe_id: str = Field(description="CWE identifier (e.g., CWE-89)")
    severity: str = Field(description="Severity: HIGH, MEDIUM, or LOW")
    title: str = Field(description="Short finding title")
    description: str = Field(description="Detailed description")
    remediation: str = Field(default="", description="Suggested fix")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score 0.0-1.0")


# Severity ordering for sort priority
_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


class SecurityReport(BaseModel):
    """Complete security review report.

    Attributes:
        findings: List of security findings.
        languages_detected: Languages detected in the project.
        patterns_loaded: Number of CWE patterns loaded.
        scan_duration_seconds: How long the scan took.
        timed_out: Whether the scan timed out.

    """

    model_config = ConfigDict(frozen=True)

    findings: list[SecurityFinding] = Field(default_factory=list)
    languages_detected: list[str] = Field(default_factory=list)
    patterns_loaded: int = Field(default=0)
    scan_duration_seconds: float = Field(default=0.0)
    timed_out: bool = Field(default=False)
    analysis_quality: Literal["full", "partial", "degraded", "failed"] = Field(
        default="full",
        description="Scan quality: full/partial/degraded/failed",
    )

    def to_cache_dict(self) -> dict[str, Any]:
        """Serialize to dict for cache JSON storage."""
        return {
            "findings": [f.model_dump() for f in self.findings],
            "languages_detected": self.languages_detected,
            "patterns_loaded": self.patterns_loaded,
            "scan_duration_seconds": self.scan_duration_seconds,
            "timed_out": self.timed_out,
            "analysis_quality": self.analysis_quality,
        }

    @classmethod
    def from_cache_dict(cls, data: dict[str, Any]) -> SecurityReport:
        """Deserialize from cache JSON dict.

        Args:
            data: Dict from cache JSON.

        Returns:
            SecurityReport instance.

        """
        findings = [SecurityFinding(**f) for f in data.get("findings", [])]
        return cls(
            findings=findings,
            languages_detected=data.get("languages_detected", []),
            patterns_loaded=data.get("patterns_loaded", 0),
            scan_duration_seconds=data.get("scan_duration_seconds", 0.0),
            timed_out=data.get("timed_out", False),
            analysis_quality=data.get("analysis_quality", "full"),
        )

    def filter_for_synthesis(
        self,
        min_confidence: float = 0.5,
        max_findings: int = 25,
    ) -> list[SecurityFinding]:
        """Filter findings for synthesis prompt inclusion.

        Applies confidence threshold and count cap, prioritized by
        severity (HIGH > MEDIUM > LOW) × confidence (descending).

        Args:
            min_confidence: Minimum confidence threshold (default 0.5).
            max_findings: Maximum number of findings to include.

        Returns:
            Filtered and prioritized list of findings.

        """
        # Filter by confidence
        filtered = [f for f in self.findings if f.confidence >= min_confidence]

        # Sort by severity priority then confidence descending
        filtered.sort(
            key=lambda f: (
                _SEVERITY_ORDER.get(f.severity.upper(), 99),
                -f.confidence,
            )
        )

        # Apply count cap
        return filtered[:max_findings]

    def to_markdown(self) -> str:
        """Render report as markdown with YAML frontmatter.

        Returns:
            Markdown string with frontmatter and findings.

        """
        lines = [
            "---",
            f"languages: {self.languages_detected}",
            f"patterns_loaded: {self.patterns_loaded}",
            f"scan_duration_seconds: {self.scan_duration_seconds:.1f}",
            f"timed_out: {self.timed_out}",
            f"analysis_quality: {self.analysis_quality}",
            f"total_findings: {len(self.findings)}",
            "---",
            "",
            "# Security Review Report",
            "",
        ]

        if not self.findings:
            lines.append("No security findings detected.")
            return "\n".join(lines)

        # Summary
        high = sum(1 for f in self.findings if f.severity.upper() == "HIGH")
        medium = sum(1 for f in self.findings if f.severity.upper() == "MEDIUM")
        low = sum(1 for f in self.findings if f.severity.upper() == "LOW")
        lines.append(f"**Summary:** {high} HIGH, {medium} MEDIUM, {low} LOW")
        lines.append("")

        # Findings sorted by severity
        sorted_findings = sorted(
            self.findings,
            key=lambda f: (
                _SEVERITY_ORDER.get(f.severity.upper(), 99),
                -f.confidence,
            ),
        )

        for f in sorted_findings:
            lines.append(f"## {f.id}: {f.title}")
            lines.append("")
            lines.append(f"- **File:** `{f.file_path}`:{f.line_number}")
            lines.append(f"- **CWE:** {f.cwe_id}")
            lines.append(f"- **Severity:** {f.severity}")
            lines.append(f"- **Confidence:** {f.confidence:.1f}")
            lines.append(f"- **Description:** {f.description}")
            if f.remediation:
                lines.append(f"- **Remediation:** {f.remediation}")
            lines.append("")

        return "\n".join(lines)
