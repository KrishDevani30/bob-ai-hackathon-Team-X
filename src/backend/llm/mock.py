"""Deterministic mock LLM client for testing and zero-key operation."""

from __future__ import annotations

import json

from backend.llm.client import LLMClient

# Deterministic severity mapping by deviation_type keyword
_TYPE_MAP: dict[str, tuple[str, str, str]] = {
    "eligibility_violation": ("Major", "Eligibility violation directly impacts patient safety and trial integrity.", "ICH E6(R2) 4.3"),
    "prohibited_medication": ("Major", "Prohibited medication poses a direct patient safety risk via drug interaction.", "ICH E6(R2) 4.5.1"),
    "missed_visit": ("Minor", "Missed visit may result in incomplete safety monitoring data.", "ICH E6(R2) 6.5.2"),
    "out_of_window": ("Minor", "Visit outside the protocol window may introduce data variability.", "ICH E6(R2) 6.5.2"),
    "dosing_deviation": ("Minor", "Dose outside the acceptable range may affect efficacy and safety assessments.", "ICH E6(R2) 6.6.1"),
    "missing_assessment": ("Minor", "Missing required assessment may result in incomplete safety data.", "ICH E6(R2) 8.3"),
    "late_data_entry": ("Administrative", "Late data entry is a procedural deviation with no direct patient impact.", "ICH E6(R2) 8.1"),
}


class MockLLMClient(LLMClient):
    """Returns a deterministic JSON classification or CAPA report based on request."""

    def complete(self, system_prompt: str, user_message: str) -> str:
        # Check if this is a CAPA generation request
        if "capa" in system_prompt.lower() or "root_cause_analysis" in system_prompt.lower():
            return json.dumps({
                "root_cause_analysis": "Root cause analysis indicates site workflow bottlenecks, scheduling variance during active dosing cycles, and delayed electronic data capture reconciliation.",
                "immediate_corrective_action": "1. Re-train site staff on protocol-specified visit windows and mandatory safety assessments.\n2. Re-verify concomitant medication logs for active protocol compliance.",
                "preventive_action": "1. Implement automated EHR/EDC calendar alerts 48 hours prior to scheduled visit windows.\n2. Mandate dual-coordinator verification before dispensing study drug.",
                "effectiveness_check_criteria": "Audit 100% of subject visits over the subsequent 60 days to verify zero unapproved protocol deviations and 100% assessment completion.",
                "regulatory_references": [
                    "ICH E6(R2) Section 4.5 Compliance with Protocol",
                    "ICH E6(R2) Section 4.9 Records and Reports",
                    "ICH E6(R2) Section 5.18 Monitoring",
                    "21 CFR 312.60 General Responsibilities of Investigators"
                ]
            })

        # Extract deviation_type from user message heuristically
        chosen = ("Minor", "Protocol deviation detected.", "ICH E6(R2) 5.0")
        for key, value in _TYPE_MAP.items():
            if key in user_message.lower():
                chosen = value
                break

        result = {
            "severity": chosen[0],
            "rationale": chosen[1],
            "ich_reference": chosen[2],
            "confidence": 0.92,
        }
        return json.dumps(result)

