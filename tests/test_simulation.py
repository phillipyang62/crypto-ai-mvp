from types import SimpleNamespace
import unittest

from ai_mvp.simulation import simulation_for


def assessment(cap="R3", prohibitions=()):
    return SimpleNamespace(
        dimensions={"FC": 63, "RA": 57, "KE": 65, "BD": 70, "OC": 83},
        product_cap=cap,
        prohibitions=prohibitions,
    )


class SimulationTests(unittest.TestCase):
    def test_each_scenario_is_within_the_cap_and_has_assessment_references(self):
        for cap in range(1, 6):
            with self.subTest(cap=cap):
                result = simulation_for(assessment(f"R{cap}"), "advisory")
                self.assertEqual(result["state"], "available")
                self.assertEqual(len(result["scenarios"]), min(cap, 2))
                for scenario in result["scenarios"]:
                    self.assertLessEqual(int(scenario["product_risk"][1:]), cap)
                    self.assertIn({"type": "product_cap", "value": f"R{cap}"}, scenario["references"])
                    self.assertTrue(any(ref["type"] == "dimension" for ref in scenario["references"]))

    def test_simulation_fails_closed_when_restrictions_are_unknown(self):
        for profile, reason in (
            (assessment(prohibitions=("unmapped_restriction",)), "upstream_prohibition"),
            (assessment("unknown"), "invalid_product_cap"),
        ):
            with self.subTest(reason=reason):
                self.assertEqual(simulation_for(profile, "advisory"), {
                    "state": "blocked", "reason": reason, "scenarios": [],
                })

    def test_simulation_requires_assessment_and_advisory_mode(self):
        self.assertEqual(simulation_for(None, "advisory")["state"], "assessment_required")
        for mode, reason in (("education", "education_mode"), ("clarify", "clarification_required")):
            with self.subTest(mode=mode):
                self.assertEqual(simulation_for(assessment(), mode), {
                    "state": "not_applicable", "reason": reason, "scenarios": [],
                })
