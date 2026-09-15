"""Pydantic v2 models for the machine-readable protocol specification."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class SeverityLevel(str, Enum):
    MAJOR = "Major"
    MINOR = "Minor"
    ADMINISTRATIVE = "Administrative"


class VisitDefinition(BaseModel):
    visit_id: str
    visit_name: str
    target_day: int
    window_before_days: int = Field(ge=0)
    window_after_days: int = Field(ge=0)
    is_safety_critical: bool
    required_assessments: list[str]


class DoseModificationRule(BaseModel):
    rule_id: str
    condition: str
    action: str
    requires_documentation: bool = True


class DosingSpec(BaseModel):
    drug_name: str
    planned_dose_mg: float = Field(gt=0)
    dose_unit: str
    frequency: str
    min_acceptable_dose_mg: float = Field(gt=0)
    max_acceptable_dose_mg: float = Field(gt=0)
    dose_modification_rules: list[DoseModificationRule] = []

    @model_validator(mode="after")
    def min_lte_max(self) -> "DosingSpec":
        if self.min_acceptable_dose_mg > self.max_acceptable_dose_mg:
            raise ValueError(
                f"min_acceptable_dose_mg ({self.min_acceptable_dose_mg}) "
                f"must be ≤ max_acceptable_dose_mg ({self.max_acceptable_dose_mg})"
            )
        return self


class ProhibitedMedication(BaseModel):
    drug_name: str
    atc_class: str
    reason: str
    severity_floor: SeverityLevel


class AssessmentDefinition(BaseModel):
    name: str
    is_safety_critical: bool


class EligibilityCriterion(BaseModel):
    criterion_id: str
    description: str
    field: str
    operator: str
    value: Any


class EligibilityCriteria(BaseModel):
    inclusion: list[EligibilityCriterion] = []
    exclusion: list[EligibilityCriterion] = []


class ProtocolSpec(BaseModel):
    study_id: str
    protocol_version: str
    title: str
    visit_schedule: list[VisitDefinition] = Field(min_length=1)
    dosing: DosingSpec
    prohibited_medications: list[ProhibitedMedication] = []
    required_assessments: dict[str, AssessmentDefinition] = {}
    eligibility_criteria: EligibilityCriteria = EligibilityCriteria()

    @model_validator(mode="after")
    def assessments_referenced_exist(self) -> "ProtocolSpec":
        known = set(self.required_assessments.keys())
        for visit in self.visit_schedule:
            missing = set(visit.required_assessments) - known
            if missing:
                raise ValueError(
                    f"Visit '{visit.visit_id}' references unknown assessment codes: {missing}"
                )
        return self
