from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from ai_mvp.profiles import ProfileStore, ProfileValidationError, normalize_profile


class NormalizeProfileTests(unittest.TestCase):
    def test_five_dimension_levels_match_rai_boundaries(self):
        for rai, level in ((0, 1), (24, 1), (25, 2), (44, 2), (45, 3),
                           (64, 3), (65, 4), (79, 4), (80, 5), (100, 5)):
            with self.subTest(rai=rai):
                profile = normalize_profile("demo", {"assessment": {
                    "dimensions": dict.fromkeys(("FC", "RA", "KE", "BD", "OC"), rai),
                    "rai": rai, "risk_level": f"C{level}", "product_cap": f"R{level}",
                }})
                self.assertEqual(profile.risk_ceiling, f"C{level}")
                self.assertEqual(profile.route, "education" if rai < 45 else "advisory")
                self.assertIsNone(profile.wealth_band)

    def test_five_dimension_rejects_inconsistent_assessment(self):
        assessment = {
            "dimensions": {"FC": 60, "RA": 60, "KE": 60, "BD": 60, "OC": 60},
            "rai": 60, "risk_level": "C3", "product_cap": "R3",
        }
        for overrides in (
            {"rai": True}, {"risk_level": "C2"}, {"product_cap": "R5"},
            {"dimensions": {**assessment["dimensions"], "FC": 0}},
            {"dimensions": {**assessment["dimensions"], "KE": float("nan")}},
            {"allocation_range": {"min_pct": 20, "max_pct": 5}},
            {"flags": ["free text"]},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ProfileValidationError):
                normalize_profile("demo", {"assessment": {**assessment, **overrides}})

    def test_knowledge_changes_route_without_raising_risk_ceiling(self):
        dimensions = {"risk_willingness": "high", "risk_capacity": "low", "wealth_band": "high"}
        novice = normalize_profile("demo", {"dimensions": {**dimensions, "knowledge_level": "low"}})
        experienced = normalize_profile("demo", {"dimensions": {**dimensions, "knowledge_level": "high"}})

        self.assertEqual(novice.route, "education")
        self.assertEqual(experienced.route, "advisory")
        self.assertEqual(novice.risk_ceiling, "low")
        self.assertEqual(experienced.risk_ceiling, novice.risk_ceiling)
        self.assertIn("risk_capacity_limits_ceiling", experienced.reasons)

    def test_missing_required_fields_do_not_invent_a_ceiling(self):
        profile = normalize_profile("demo", {"dimensions": {"risk_willingness": "high"}})

        self.assertEqual(profile.route, "clarify")
        self.assertIsNone(profile.risk_ceiling)
        self.assertEqual(profile.missing_fields, ("risk_capacity", "wealth_band", "knowledge_level"))
        self.assertEqual(profile.reasons, ("missing_risk_capacity", "missing_knowledge_level"))

    def test_missing_wealth_is_optional_and_raw_answers_are_ignored(self):
        profile = normalize_profile(
            "demo",
            {
                "questionnaire_version": "q2",
                "source": "questionnaire-ui",
                "raw_answers": "private questionnaire input",
                "dimensions": {
                    "risk_willingness": "medium",
                    "risk_capacity": "high",
                    "knowledge_level": "medium",
                    "private_answer": "private questionnaire input",
                },
            },
        )

        self.assertEqual(profile.route, "advisory")
        self.assertEqual(profile.risk_ceiling, "medium")
        self.assertEqual(profile.questionnaire_version, "q2")
        self.assertEqual(profile.source, "questionnaire-ui")
        self.assertEqual(profile.as_dict()["missing_fields"], ["wealth_band"])
        self.assertNotIn("private questionnaire input", str(profile.as_dict()))

    def test_invalid_shapes_and_values_are_rejected(self):
        for questionnaire in (
            [],
            {"dimensions": "high"},
            {"dimensions": {"risk_capacity": []}},
            {"dimensions": {"knowledge_level": "expert"}},
            {"questionnaire_version": "untrusted\ntext"},
        ):
            with self.subTest(questionnaire=questionnaire), self.assertRaises(ProfileValidationError):
                normalize_profile("demo", questionnaire)


class ProfileStoreTests(unittest.TestCase):
    def test_new_assessment_and_old_profiles_share_the_existing_sqlite_format(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.sqlite3"
            store = ProfileStore(path)
            legacy = store.upsert("old", {"dimensions": {"risk_willingness": "low"}})
            profile = store.upsert("new", {"raw_answers": "private", "assessment": {
                "dimensions": {"FC": 62.5, "RA": 65, "KE": 75, "BD": 60, "OC": 55},
                "rai": 63, "risk_level": "C3", "product_cap": "R3",
                "allocation_range": {"min_pct": 5, "max_pct": 15},
                "flags": ["short_horizon"], "prohibitions": ["no_leverage"],
            }})
            reopened = ProfileStore(path)
            self.assertEqual(reopened.get("old"), legacy)
            self.assertNotIn("assessment", legacy.as_dict())
            self.assertEqual(reopened.get("new"), profile)
            self.assertEqual(profile.as_dict()["assessment"]["flags"], ["short_horizon"])
            self.assertNotIn("private", str(profile.as_dict()))

    def test_updates_persist_across_instances_and_invalid_updates_leave_state_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.sqlite3"
            store = ProfileStore(path)
            self.assertIsNone(store.get("demo"))
            store.upsert("demo", {"questionnaire_version": "q1"})
            replacement = store.upsert("demo", {"questionnaire_version": "q2"})

            with self.assertRaises(ProfileValidationError):
                store.upsert("demo", {"dimensions": {"risk_capacity": 1}})

            self.assertEqual(ProfileStore(path).get("demo"), replacement)

    def test_shared_store_uses_a_connection_for_each_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(Path(directory) / "profiles.sqlite3")
            with ThreadPoolExecutor(max_workers=4) as executor:
                user_ids = [f"demo-{index}" for index in range(8)]
                expected = list(executor.map(lambda user_id: store.upsert(user_id, {}), user_ids))
                actual = list(executor.map(store.get, user_ids))

            self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
