"""Hybrid severity classifier: deterministic rule overrides + LLM classification.

Flow:
  1. Call LLM (or use fallback) → raw_severity
  2. Apply deterministic rule overrides that can only RAISE severity
  3. Log each classification with its source for auditability
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from pydantic import BaseModel, ValidationError

from backend.classification.prompts import SEVERITY_SYSTEM_PROMPT
from backend.detection.models import Deviation
from backend.llm.client import LLMClient
from backend.protocol.schema import ProtocolSpec, SeverityLevel

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {
    SeverityLevel.ADMINISTRATIVE: 0,
    SeverityLevel.MINOR: 1,
    SeverityLevel.MAJOR: 2,
}


class ClassificationResult(BaseModel):
    severity: SeverityLevel
    rationale: str
    ich_reference: str
    confidence: float
    source: str  # "llm" | "rule_override" | "fallback"


def _severity_from_str(s: str) -> SeverityLevel:
    mapping = {v.value.lower(): v for v in SeverityLevel}
    return mapping.get(s.lower().strip(), SeverityLevel.MINOR)


def _raise_severity(
    current: SeverityLevel, floor: SeverityLevel
) -> SeverityLevel:
    """Return the higher of current and floor — never lower."""
    if _SEVERITY_ORDER[floor] > _SEVERITY_ORDER[current]:
        return floor
    return current


def _context_hash(deviation: Deviation) -> str:
    key = json.dumps(
        {
            "rule_id": deviation.rule_id,
            "deviation_type": deviation.deviation_type,
            "context": deviation.context,
            "magnitude": deviation.magnitude,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(key.encode()).hexdigest()


def _build_user_message(deviation: Deviation, prior_history: list[str]) -> str:
    parts = [
        f"deviation_type: {deviation.deviation_type}",
        f"rule_id: {deviation.rule_id}",
        f"is_safety_critical: {deviation.is_safety_critical}",
        f"magnitude: {deviation.magnitude}",
        f"context: {json.dumps(deviation.context, default=str)}",
        f"patient_prior_deviations_at_site: {prior_history}",
    ]
    return "\n".join(parts)


def _parse_llm_response(raw: str) -> ClassificationResult | None:
    """Attempt to parse LLM JSON response into ClassificationResult."""
    try:
        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(
                line for line in cleaned.splitlines()
                if not line.startswith("```")
            )
        data = json.loads(cleaned)
        result = ClassificationResult.model_validate({**data, "source": "llm"})
        return result
    except (json.JSONDecodeError, ValidationError, KeyError):
        return None


class SeverityClassifier:
    """Classifies deviation severity using LLM + mandatory rule overrides."""

    def __init__(self, llm: LLMClient, spec: ProtocolSpec) -> None:
        self._llm = llm
        self._spec = spec
        self._cache: dict[str, ClassificationResult] = {}
        self._prohibited_floor: dict[str, SeverityLevel] = {
            m.drug_name.lower(): m.severity_floor
            for m in spec.prohibited_medications
        }
        self._prohibited_floor.update({
            m.atc_class.lower(): m.severity_floor
            for m in spec.prohibited_medications
        })

    def classify(
        self,
        deviation: Deviation,
        prior_history: list[str] | None = None,
    ) -> ClassificationResult:
        """Return a ClassificationResult for *deviation*.

        Results are cached by context hash.  Rule overrides are always applied
        after LLM classification and can only raise severity.
        """
        cache_key = _context_hash(deviation)
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = self._llm_classify(deviation, prior_history or [])
        result = self._apply_rule_overrides(deviation, result)

        self._cache[cache_key] = result
        logger.info(
            "classified deviation_id=%s type=%s → %s (source=%s)",
            deviation.deviation_id,
            deviation.deviation_type,
            result.severity.value,
            result.source,
        )
        return result

    def classify_batch(
        self,
        deviations: list[Deviation],
        prior_history_map: dict[str, list[str]] | None = None,
    ) -> list[ClassificationResult]:
        """Classify a list of deviations, reusing cache where possible."""
        results = []
        for dev in deviations:
            history = (prior_history_map or {}).get(dev.patient_id, [])
            results.append(self.classify(dev, history))
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _llm_classify(
        self, deviation: Deviation, prior_history: list[str]
    ) -> ClassificationResult:
        user_msg = _build_user_message(deviation, prior_history)
        fallback_severity = self._fallback_severity(deviation)

        for attempt in range(2):
            try:
                raw = self._llm.complete(SEVERITY_SYSTEM_PROMPT, user_msg)
                parsed = _parse_llm_response(raw)
                if parsed is not None:
                    return parsed
                logger.warning(
                    "LLM parse failure attempt=%d deviation_id=%s",
                    attempt + 1,
                    deviation.deviation_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "LLM call failed attempt=%d deviation_id=%s error=%s",
                    attempt + 1,
                    deviation.deviation_id,
                    exc,
                )

        # Both attempts failed — use conservative fallback
        logger.error(
            "Using fallback severity for deviation_id=%s", deviation.deviation_id
        )
        return ClassificationResult(
            severity=fallback_severity,
            rationale="Fallback classification: LLM unavailable or returned invalid response.",
            ich_reference="ICH E6(R2) 5.0",
            confidence=0.5,
            source="fallback",
        )

    def _fallback_severity(self, deviation: Deviation) -> SeverityLevel:
        """Conservative rule-only severity used when LLM fails."""
        if deviation.deviation_type == "eligibility_violation":
            return SeverityLevel.MAJOR
        if deviation.deviation_type == "prohibited_medication":
            return SeverityLevel.MAJOR
        if deviation.is_safety_critical:
            return SeverityLevel.MINOR
        if deviation.deviation_type == "late_data_entry":
            return SeverityLevel.ADMINISTRATIVE
        return SeverityLevel.MINOR

    def _apply_rule_overrides(
        self, deviation: Deviation, result: ClassificationResult
    ) -> ClassificationResult:
        """Apply deterministic overrides — can only raise, never lower severity."""
        original = result.severity
        severity = result.severity
        override_reasons: list[str] = []

        # Eligibility violations are always Major
        if deviation.deviation_type == "eligibility_violation":
            severity = _raise_severity(severity, SeverityLevel.MAJOR)
            if severity != original:
                override_reasons.append("Eligibility violation → always Major (ICH E6(R2) 4.3)")

        # Prohibited medication inherits its severity_floor
        if deviation.deviation_type == "prohibited_medication":
            floor_raw = deviation.context.get("severity_floor", "Major")
            floor = _severity_from_str(floor_raw)
            severity = _raise_severity(severity, floor)
            if severity != original:
                override_reasons.append(
                    f"Prohibited medication floor={floor.value} (ICH E6(R2) 4.5.1)"
                )

        # Any deviation on a safety-critical visit/assessment is at least Minor
        if deviation.is_safety_critical:
            severity = _raise_severity(severity, SeverityLevel.MINOR)
            if severity != original:
                override_reasons.append(
                    "Safety-critical visit/assessment → minimum Minor (ICH E6(R2) 8.3)"
                )

        if not override_reasons:
            return result

        updated_rationale = result.rationale
        if override_reasons:
            updated_rationale += " [OVERRIDE: " + "; ".join(override_reasons) + "]"

        return ClassificationResult(
            severity=severity,
            rationale=updated_rationale,
            ich_reference=result.ich_reference,
            confidence=result.confidence,
            source="rule_override",
        )
