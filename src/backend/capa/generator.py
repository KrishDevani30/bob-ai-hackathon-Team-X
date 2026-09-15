"""CAPA report content generation using LLM (grounded in data only)."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from pydantic import BaseModel, ValidationError

from backend.classification.prompts import CAPA_SYSTEM_PROMPT
from backend.detection.models import Deviation
from backend.llm.client import LLMClient
from backend.protocol.schema import SeverityLevel
from backend.risk.scoring import SiteRiskScore

logger = logging.getLogger(__name__)


class CapaContent(BaseModel):
    root_cause_analysis: str
    immediate_corrective_action: str
    preventive_action: str
    effectiveness_check_criteria: str
    regulatory_references: list[str]


class CapaReport(BaseModel):
    study_id: str
    protocol_version: str
    site_id: str
    pi_name: str
    report_date: str
    risk_band: str
    risk_score: float
    total_deviations: int
    deviation_summary: list[dict[str, Any]]
    capa_content: CapaContent
    generated_by: str = "CTRM System (Draft — requires qualified human review)"


def _build_deviation_summary(deviations: list[Deviation]) -> list[dict[str, Any]]:
    from collections import Counter
    type_counts: Counter = Counter()
    severity_dist: dict[str, Counter] = {}

    for d in deviations:
        type_counts[d.deviation_type] += 1
        if d.deviation_type not in severity_dist:
            severity_dist[d.deviation_type] = Counter()
        severity_dist[d.deviation_type][d.severity or "Unclassified"] += 1

    summary = []
    for dev_type, count in type_counts.most_common():
        affected_patients = len({d.patient_id for d in deviations if d.deviation_type == dev_type})
        summary.append({
            "deviation_type": dev_type,
            "count": count,
            "affected_patients": affected_patients,
            "severity_distribution": dict(severity_dist[dev_type]),
        })
    return summary


def _build_llm_user_message(
    site_id: str,
    deviations: list[Deviation],
    risk_score: SiteRiskScore,
    site_info: dict[str, Any],
) -> str:
    summary = _build_deviation_summary(deviations)
    major_count = sum(1 for d in deviations if d.severity == SeverityLevel.MAJOR.value)
    minor_count = sum(1 for d in deviations if d.severity == SeverityLevel.MINOR.value)
    admin_count = sum(1 for d in deviations if d.severity == SeverityLevel.ADMINISTRATIVE.value)

    return json.dumps({
        "site_id": site_id,
        "site_name": site_info.get("site_name", "Unknown"),
        "country": site_info.get("country", "Unknown"),
        "principal_investigator": site_info.get("principal_investigator", "Unknown"),
        "risk_score": risk_score.score,
        "risk_band": risk_score.risk_band,
        "total_visits": risk_score.total_visits,
        "total_deviations": risk_score.total_deviations,
        "major_count": major_count,
        "minor_count": minor_count,
        "admin_count": admin_count,
        "deviation_summary": summary,
        "top_indicators": [
            {"name": ind.name, "contribution": ind.contribution}
            for ind in sorted(risk_score.indicators, key=lambda x: x.contribution, reverse=True)[:3]
        ],
    }, indent=2, default=str)


def generate_capa(
    site_id: str,
    deviations: list[Deviation],
    risk_score: SiteRiskScore,
    site_info: dict[str, Any],
    study_id: str,
    protocol_version: str,
    llm: LLMClient,
) -> CapaReport:
    """Generate a CAPA report for *site_id*.

    All narrative sections are grounded in provided data.
    """
    user_msg = _build_llm_user_message(site_id, deviations, risk_score, site_info)

    capa_content: CapaContent | None = None
    for attempt in range(2):
        try:
            raw = llm.complete(CAPA_SYSTEM_PROMPT, user_msg)
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = "\n".join(
                    line for line in cleaned.splitlines()
                    if not line.startswith("```")
                )
            data = json.loads(cleaned)
            capa_content = CapaContent.model_validate(data)
            break
        except (json.JSONDecodeError, ValidationError, Exception) as exc:
            logger.warning("CAPA LLM attempt=%d failed: %s", attempt + 1, exc)

    if capa_content is None:
        capa_content = _fallback_capa(site_id, deviations, risk_score)
        logger.error("Using fallback CAPA content for site=%s", site_id)

    return CapaReport(
        study_id=study_id,
        protocol_version=protocol_version,
        site_id=site_id,
        pi_name=site_info.get("principal_investigator", "Unknown"),
        report_date=date.today().isoformat(),
        risk_band=risk_score.risk_band,
        risk_score=risk_score.score,
        total_deviations=risk_score.total_deviations,
        deviation_summary=_build_deviation_summary(deviations),
        capa_content=capa_content,
    )


def _fallback_capa(
    site_id: str,
    deviations: list[Deviation],
    risk_score: SiteRiskScore,
) -> CapaContent:
    """Conservative fallback CAPA when LLM is unavailable."""
    top_types = list({d.deviation_type for d in deviations})[:3]
    return CapaContent(
        root_cause_analysis=(
            f"Site {site_id} has {risk_score.total_deviations} detected deviations "
            f"(risk band: {risk_score.risk_band}). "
            f"Predominant deviation types: {', '.join(top_types)}. "
            "Detailed root cause analysis requires qualified review. [DATA REQUIRED]"
        ),
        immediate_corrective_action=(
            "Immediate actions: (1) Notify Principal Investigator of all open deviations. "
            "(2) Review all Major deviations with site staff within 5 business days. "
            "(3) Correct or document all affected CRF entries per sponsor SOPs. "
            "[Responsible party and completion date: DATA REQUIRED]"
        ),
        preventive_action=(
            "Preventive actions: (1) Conduct targeted retraining on protocol requirements. "
            "(2) Implement visit scheduling reminder system. "
            "(3) Increase monitoring visit frequency. "
            "[Detailed actions require site assessment: DATA REQUIRED]"
        ),
        effectiveness_check_criteria=(
            "The CAPA will be considered effective if: "
            "(1) Zero repeat deviations of the same type occur over the next 90 days. "
            "(2) Data entry lag reduces to ≤3 business days. "
            "(3) Site risk score decreases to Medium band or below. "
            "[Verification timeline: DATA REQUIRED]"
        ),
        regulatory_references=[
            "ICH E6(R2) Section 5.19 — Monitoring",
            "ICH E6(R2) Section 4.5 — Compliance with Protocol",
            "21 CFR Part 312.62 — Investigator Recordkeeping",
            "21 CFR Part 312.68 — Inspection of Investigator Records",
        ],
    )
