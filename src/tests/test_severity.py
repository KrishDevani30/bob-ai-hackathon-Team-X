"""Tests for the severity classifier — rule overrides and LLM fallback."""

from __future__ import annotations

import json

import pytest

from backend.classification.severity import SeverityClassifier, ClassificationResult
from backend.detection.models import Deviation
from backend.protocol.schema import SeverityLevel


def _make_deviation(**kwargs) -> Deviation:
    defaults = {
        "patient_id": "P001",
        "site_id": "SITE-001",
        "visit_id": "V-001",
        "rule_id": "R001",
        "deviation_type": "out_of_window",
        "is_safety_critical": False,
        "context": {},
    }
    defaults.update(kwargs)
    return Deviation(**defaults)


class TestRuleOverrides:
    """Rule overrides must raise severity but never lower it."""

    def test_eligibility_violation_always_major(self, mock_llm, protocol_spec):
        # LLM would return Minor — override must raise to Major
        class MinorLLM:
            def complete(self, sys, usr):
                return json.dumps({
                    "severity": "Minor",
                    "rationale": "test",
                    "ich_reference": "ICH E6",
                    "confidence": 0.9,
                })

        clf = SeverityClassifier(MinorLLM(), protocol_spec)
        dev = _make_deviation(
            deviation_type="eligibility_violation",
            is_safety_critical=True,
            context={"criterion_id": "INC01"},
        )
        result = clf.classify(dev)
        assert result.severity == SeverityLevel.MAJOR
        assert result.source == "rule_override"

    def test_prohibited_medication_floor_major(self, mock_llm, protocol_spec):
        class AdminLLM:
            def complete(self, sys, usr):
                return json.dumps({
                    "severity": "Administrative",
                    "rationale": "test",
                    "ich_reference": "ICH E6",
                    "confidence": 0.8,
                })

        clf = SeverityClassifier(AdminLLM(), protocol_spec)
        dev = _make_deviation(
            deviation_type="prohibited_medication",
            is_safety_critical=True,
            context={"severity_floor": "Major", "drug_name": "Warfarin"},
        )
        result = clf.classify(dev)
        assert result.severity == SeverityLevel.MAJOR

    def test_safety_critical_at_least_minor(self, mock_llm, protocol_spec):
        class AdminLLM:
            def complete(self, sys, usr):
                return json.dumps({
                    "severity": "Administrative",
                    "rationale": "late entry",
                    "ich_reference": "ICH E6(R2) 8.1",
                    "confidence": 0.85,
                })

        clf = SeverityClassifier(AdminLLM(), protocol_spec)
        dev = _make_deviation(
            deviation_type="out_of_window",
            is_safety_critical=True,
            context={},
        )
        result = clf.classify(dev)
        assert result.severity in (SeverityLevel.MINOR, SeverityLevel.MAJOR)

    def test_override_never_lowers_major_to_minor(self, mock_llm, protocol_spec):
        """If LLM says Major and rule would normally floor to Minor, stay Major."""
        class MajorLLM:
            def complete(self, sys, usr):
                return json.dumps({
                    "severity": "Major",
                    "rationale": "safety risk",
                    "ich_reference": "ICH E6(R2) 4.5",
                    "confidence": 0.95,
                })

        clf = SeverityClassifier(MajorLLM(), protocol_spec)
        dev = _make_deviation(
            deviation_type="missed_visit",
            is_safety_critical=True,
            context={},
        )
        result = clf.classify(dev)
        # Safety-critical floor is Minor — but LLM already said Major; must stay Major
        assert result.severity == SeverityLevel.MAJOR

    def test_malformed_llm_json_triggers_retry_then_fallback(self, protocol_spec):
        """Malformed LLM JSON must not crash — falls back to conservative severity."""
        call_count = 0

        class BadLLM:
            def complete(self, sys, usr):
                nonlocal call_count
                call_count += 1
                return "NOT VALID JSON {"

        clf = SeverityClassifier(BadLLM(), protocol_spec)
        dev = _make_deviation(
            deviation_type="missed_visit",
            is_safety_critical=True,
        )
        result = clf.classify(dev)
        # Both attempts failed → fallback
        assert result.source == "fallback"
        assert result.severity in (SeverityLevel.MINOR, SeverityLevel.MAJOR)
        assert call_count == 2  # Exactly 2 attempts

    def test_cache_prevents_redundant_llm_calls(self, protocol_spec):
        call_count = 0

        class CountingLLM:
            def complete(self, sys, usr):
                nonlocal call_count
                call_count += 1
                return json.dumps({
                    "severity": "Minor",
                    "rationale": "test",
                    "ich_reference": "ICH E6",
                    "confidence": 0.9,
                })

        clf = SeverityClassifier(CountingLLM(), protocol_spec)
        dev = _make_deviation(deviation_type="out_of_window", context={"delta": 5})

        # Classify same deviation twice
        clf.classify(dev)
        clf.classify(dev)

        assert call_count == 1  # Second call should hit cache


class TestMockLLMClassifier:
    def test_mock_llm_classifies_all_types(self, mock_llm, protocol_spec):
        clf = SeverityClassifier(mock_llm, protocol_spec)
        for dev_type in [
            "out_of_window", "missed_visit", "dosing_deviation",
            "missing_assessment", "late_data_entry",
        ]:
            dev = _make_deviation(deviation_type=dev_type)
            result = clf.classify(dev)
            assert result.severity in SeverityLevel.__members__.values()

    def test_eligibility_override_with_mock(self, mock_llm, protocol_spec):
        clf = SeverityClassifier(mock_llm, protocol_spec)
        dev = _make_deviation(
            deviation_type="eligibility_violation",
            is_safety_critical=True,
            context={"criterion_id": "INC01"},
        )
        result = clf.classify(dev)
        assert result.severity == SeverityLevel.MAJOR
