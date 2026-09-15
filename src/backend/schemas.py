"""Pydantic v2 request/response schemas for the FastAPI layer."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    protocol_path: str | None = None  # defaults to bundled spec
    data_dir: str | None = None       # defaults to bundled data dir


class IngestResponse(BaseModel):
    sites_loaded: int
    patients_loaded: int
    visits_loaded: int
    conmeds_loaded: int


# ---------------------------------------------------------------------------
# Deviations
# ---------------------------------------------------------------------------

class DeviationOut(BaseModel):
    deviation_id: str
    patient_id: str
    site_id: str
    visit_id: str | None
    rule_id: str
    deviation_type: str
    detected_at: datetime
    magnitude: float | None
    context: dict[str, Any]
    is_safety_critical: bool
    severity: str | None
    severity_source: str | None
    rationale: str | None

    model_config = {"from_attributes": True}


class DeviationListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[DeviationOut]


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------

class IndicatorBreakdownOut(BaseModel):
    name: str
    raw_value: float
    normalized_value: float
    weight: float
    contribution: float


class SiteRiskOut(BaseModel):
    site_id: str
    score: float
    risk_band: str
    total_deviations: int
    total_visits: int
    indicators: list[IndicatorBreakdownOut] = []

    model_config = {"from_attributes": True}


class SiteRiskListResponse(BaseModel):
    items: list[SiteRiskOut]


# ---------------------------------------------------------------------------
# Site detail
# ---------------------------------------------------------------------------

class SiteDetailOut(BaseModel):
    site_id: str
    site_name: str
    country: str
    principal_investigator: str | None
    activation_date: str | None
    staff_count: int | None
    monitoring_visits_completed: int | None
    risk_score: SiteRiskOut | None
    recent_deviations: list[DeviationOut]


# ---------------------------------------------------------------------------
# CAPA
# ---------------------------------------------------------------------------

class CapaGenerateResponse(BaseModel):
    site_id: str
    report_date: str
    risk_band: str
    download_docx: str
    download_pdf: str
    narrative_preview: dict[str, Any]


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------

class AnalyzeResponse(BaseModel):
    deviations_detected: int
    sites_scored: int
    elapsed_seconds: float
