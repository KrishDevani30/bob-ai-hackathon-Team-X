"""Core Deviation data model used throughout the detection and classification pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from pydantic import BaseModel, Field


class Deviation(BaseModel):
    deviation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    patient_id: str
    site_id: str
    visit_id: str | None
    rule_id: str
    deviation_type: str
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    magnitude: float | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    is_safety_critical: bool = False
    severity: str | None = None  # set by classifier
    severity_source: str | None = None  # "llm" | "rule_override" | "fallback"
    rationale: str | None = None
