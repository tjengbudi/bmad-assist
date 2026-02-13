"""Tests for QAConfig model fields."""

import pytest
from pydantic import ValidationError

from bmad_assist.core.config.models.features import QAConfig


class TestQAConfigDefaults:
    """Tests for QAConfig default values."""

    def test_remediate_max_issues_default(self) -> None:
        """Default remediate_max_issues is 200."""
        config = QAConfig()
        assert config.remediate_max_issues == 200

    def test_other_defaults(self) -> None:
        """Other QAConfig fields have expected defaults."""
        config = QAConfig()
        assert config.check_on_startup is True
        assert config.generate_after_retro is True
        assert config.remediate_max_iterations == 2
        assert config.remediate_max_age_days == 7
        assert config.remediate_safety_cap == 0.8


class TestQAConfigValidation:
    """Tests for QAConfig field validation."""

    def test_remediate_max_issues_minimum(self) -> None:
        """remediate_max_issues minimum is 10."""
        with pytest.raises(ValidationError) as exc_info:
            QAConfig(remediate_max_issues=9)
        assert "greater than or equal to 10" in str(exc_info.value).lower()

    def test_remediate_max_issues_maximum(self) -> None:
        """remediate_max_issues maximum is 1000."""
        with pytest.raises(ValidationError) as exc_info:
            QAConfig(remediate_max_issues=1001)
        assert "less than or equal to 1000" in str(exc_info.value).lower()

    def test_remediate_max_issues_valid_values(self) -> None:
        """Valid remediate_max_issues values are accepted."""
        for value in [10, 50, 100, 200, 500, 1000]:
            config = QAConfig(remediate_max_issues=value)
            assert config.remediate_max_issues == value

    def test_remediate_max_iterations_validation(self) -> None:
        """remediate_max_iterations must be 1-5."""
        QAConfig(remediate_max_iterations=1)
        QAConfig(remediate_max_iterations=5)
        with pytest.raises(ValidationError):
            QAConfig(remediate_max_iterations=0)
        with pytest.raises(ValidationError):
            QAConfig(remediate_max_iterations=6)

    def test_remediate_max_age_days_validation(self) -> None:
        """remediate_max_age_days must be 1-30."""
        QAConfig(remediate_max_age_days=1)
        QAConfig(remediate_max_age_days=30)
        with pytest.raises(ValidationError):
            QAConfig(remediate_max_age_days=0)
        with pytest.raises(ValidationError):
            QAConfig(remediate_max_age_days=31)

    def test_remediate_safety_cap_validation(self) -> None:
        """remediate_safety_cap must be 0.1-1.0."""
        QAConfig(remediate_safety_cap=0.1)
        QAConfig(remediate_safety_cap=1.0)
        with pytest.raises(ValidationError):
            QAConfig(remediate_safety_cap=0.0)
        with pytest.raises(ValidationError):
            QAConfig(remediate_safety_cap=1.1)


class TestQAConfigFrozen:
    """Tests for QAConfig immutability."""

    def test_frozen_model(self) -> None:
        """QAConfig is immutable."""
        config = QAConfig()
        with pytest.raises(ValidationError):
            config.remediate_max_issues = 500  # type: ignore[misc]
