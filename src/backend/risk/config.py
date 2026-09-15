"""Risk scoring configuration — weights are tunable by risk managers."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RiskWeightsConfig(BaseModel):
    severity_weighted_rate: float = Field(default=0.35, ge=0, le=1)
    recurrence_rate: float = Field(default=0.20, ge=0, le=1)
    mean_time_to_detection: float = Field(default=0.15, ge=0, le=1)
    deviation_trend: float = Field(default=0.15, ge=0, le=1)
    enrollment_velocity_risk: float = Field(default=0.10, ge=0, le=1)
    monitoring_coverage: float = Field(default=0.05, ge=0, le=1)

    def validate_sum(self) -> None:
        total = (
            self.severity_weighted_rate
            + self.recurrence_rate
            + self.mean_time_to_detection
            + self.deviation_trend
            + self.enrollment_velocity_risk
            + self.monitoring_coverage
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Risk weights must sum to 1.0, got {total:.4f}")


DEFAULT_WEIGHTS = RiskWeightsConfig()

RISK_BANDS = [
    (0, 29, "Low"),
    (30, 59, "Medium"),
    (60, 79, "High"),
    (80, 100, "Critical"),
]


def get_risk_band(score: float) -> str:
    for low, high, label in RISK_BANDS:
        if low <= score <= high:
            return label
    return "Critical"
