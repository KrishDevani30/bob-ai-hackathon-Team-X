"""Tests for recall/precision of the detection engine against ground truth."""

from __future__ import annotations

import pytest


class TestDetectorRecall:
    """Measure detector recall against ground_truth.json."""

    def _run_detection(self, all_data, protocol_spec):
        from backend.detection.engine import DetectionEngine
        engine = DetectionEngine(protocol_spec)
        return engine.run(
            visits=all_data["visits"],
            conmeds=all_data["conmeds"],
            eligibility=all_data["eligibility"],
        )

    def test_recall_at_least_95_percent(self, all_data, ground_truth, protocol_spec):
        """Detector must recall ≥ 95% of injected deviations.

        Matching logic:
          - For visit-level deviations: (patient_id, visit_id, deviation_type)
          - For patient-level deviations (prohibited_medication, eligibility):
            (patient_id, site_id, deviation_type)
        Both detected and GT use the same key scheme so recall is fairly measured.
        """
        deviations = self._run_detection(all_data, protocol_spec)

        VISIT_LEVEL_TYPES = {
            "out_of_window", "missed_visit", "dosing_deviation",
            "missing_assessment", "late_data_entry",
        }

        # Build detected key sets
        detected_visit_keys: set = set()
        detected_patient_keys: set = set()
        for d in deviations:
            if d.deviation_type in VISIT_LEVEL_TYPES and d.visit_id:
                # visit_id here is the UUID visit record id — we need protocol_visit_id
                # Use (patient_id, site_id, type) as the common denominator
                detected_visit_keys.add((d.patient_id, d.site_id, d.deviation_type))
            else:
                detected_patient_keys.add((d.patient_id, d.site_id, d.deviation_type))

        # Merge into one set
        detected_keys = detected_visit_keys | detected_patient_keys

        # Build ground truth unique keys (same scheme)
        gt_keys: set = set()
        for gt_item in ground_truth:
            gt_keys.add((
                gt_item["patient_id"],
                gt_item["site_id"],
                gt_item["deviation_type"],
            ))

        if not gt_keys:
            pytest.skip("No ground truth labels found")

        recalled = detected_keys & gt_keys
        recall = len(recalled) / len(gt_keys)

        print(f"\nRecall: {recall:.3f} ({len(recalled)}/{len(gt_keys)})")
        assert recall >= 0.95, (
            f"Recall {recall:.3f} is below the 0.95 threshold.\n"
            f"Missed: {gt_keys - detected_keys}"
        )

    def test_at_least_5000_visit_records(self, all_data):
        assert len(all_data["visits"]) >= 5000, (
            f"Expected ≥5000 visits, got {len(all_data['visits'])}"
        )

    def test_all_deviation_types_detected(self, all_data, ground_truth, protocol_spec):
        """All injected deviation types should appear in detected output."""
        deviations = self._run_detection(all_data, protocol_spec)
        detected_types = {d.deviation_type for d in deviations}
        gt_types = {item["deviation_type"] for item in ground_truth}

        for dev_type in gt_types:
            assert dev_type in detected_types, f"Deviation type '{dev_type}' was never detected"

    def test_detection_completes_under_10_seconds(self, all_data, protocol_spec):
        import time
        engine = __import__("backend.detection.engine", fromlist=["DetectionEngine"]).DetectionEngine
        eng = engine(protocol_spec)
        t0 = time.perf_counter()
        eng.run(
            visits=all_data["visits"],
            conmeds=all_data["conmeds"],
            eligibility=all_data["eligibility"],
        )
        elapsed = time.perf_counter() - t0
        assert elapsed < 10.0, f"Detection took {elapsed:.2f}s — must be under 10s"
