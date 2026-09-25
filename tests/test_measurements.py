import tempfile, unittest
from pathlib import Path
from src.domain import ConflictError, PermissionDenied, ValidationError
from src.repository import Repository
from src.service import Service
from src.rules import STATES, TRANSITION_ROLES


class MeasurementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(str(Path(self.tmp.name) / "test.db"))
        self.service = Service(self.repo)
        self.item = self.service.create_item(
            {"title": "retest item", "description": "dose retest flow",
             "severity": "low", "quantity": 1, "threshold": 10,
             "external_ref": "RT-1"}, "creator", "dosimetrist")

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def _to_reviewing(self):
        current = self.service.get_item(self.item["id"], "viewer")
        return self.service.transition(
            current["id"], STATES[1], current["version"], "reviewer",
            TRANSITION_ROLES[STATES[1]][0])

    def test_measurement_fields_and_history(self):
        m1 = self.service.add_measurement(
            self.item["id"],
            {"measured_at": "2026-09-25T08:00:00+00:00", "dose": 5,
             "conclusion": "pending", "measured_by": "lab-a"},
            "recorder", "radiation_officer")
        self.assertEqual((m1["dose"], m1["conclusion"], m1["measured_by"]),
                         (5.0, "pending", "lab-a"))
        m2 = self.service.add_measurement(
            self.item["id"], {"dose": 12, "conclusion": "exceeded",
                              "measured_by": "lab-b"},
            "recorder", "dosimetrist")
        history = self.service.list_measurements(self.item["id"], "viewer")
        self.assertEqual([m["id"] for m in history], [m1["id"], m2["id"]])
        view = self.service.get_item(self.item["id"], "viewer")
        self.assertEqual(view["original_quantity"], 1)
        self.assertEqual(view["effective_dose"], 12)
        self.assertEqual(view["latest_conclusion"], "exceeded")
        self.assertTrue(view["escalation_required"])
        self.assertFalse(view["retests_pending"])

    def test_last_conclusion_overrides_high_dose(self):
        # 早先超限值留在历史里；最后一次normal使调查门槛不再触发
        self.service.add_measurement(
            self.item["id"], {"dose": 50, "conclusion": "exceeded",
                              "measured_by": "lab-a"},
            "recorder", "radiation_officer")
        self.service.add_measurement(
            self.item["id"], {"dose": 1, "conclusion": "normal",
                              "measured_by": "lab-b"},
            "recorder", "radiation_officer")
        view = self.service.get_item(self.item["id"], "viewer")
        self.assertFalse(view["escalation_required"])
        self.assertEqual(view["effective_dose"], 1)
        history = self.service.list_measurements(self.item["id"], "viewer")
        self.assertEqual(history[0]["dose"], 50)

    def test_pending_keeps_event_in_reviewing(self):
        self._to_reviewing()
        self.service.add_measurement(
            self.item["id"], {"dose": 3, "conclusion": "pending",
                              "measured_by": "lab-a"},
            "recorder", "radiation_officer")
        current = self.service.get_item(self.item["id"], "viewer")
        with self.assertRaises(ConflictError):
            self.service.transition(
                current["id"], STATES[2], current["version"], "reviewer",
                TRANSITION_ROLES[STATES[2]][0])
        # 没有任何检测时同样不能离开复核
        self.service.add_measurement(
            self.item["id"], {"dose": 3, "conclusion": "normal",
                              "measured_by": "lab-a"},
            "recorder", "radiation_officer")
        current = self.service.get_item(self.item["id"], "viewer")
        moved = self.service.transition(
            current["id"], STATES[2], current["version"], "reviewer",
            TRANSITION_ROLES[STATES[2]][0])
        self.assertEqual(moved["status"], "investigation")

    def test_no_measurement_also_blocks_investigation(self):
        current = self._to_reviewing()
        with self.assertRaises(ConflictError):
            self.service.transition(
                current["id"], STATES[2], current["version"], "reviewer",
                TRANSITION_ROLES[STATES[2]][0])

    def test_follow_up_required_before_close(self):
        self._to_reviewing()
        self.service.add_measurement(
            self.item["id"], {"dose": 12, "conclusion": "exceeded",
                              "measured_by": "lab-a"},
            "recorder", "radiation_officer")
        current = self.service.get_item(self.item["id"], "viewer")
        current = self.service.transition(
            current["id"], STATES[2], current["version"], "reviewer",
            TRANSITION_ROLES[STATES[2]][0])
        current = self.service.transition(
            current["id"], STATES[3], current["version"], "reviewer",
            TRANSITION_ROLES[STATES[3]][0])
        with self.assertRaises(ConflictError):
            self.service.transition(
                current["id"], STATES[4], current["version"], "hp",
                TRANSITION_ROLES[STATES[4]][0])
        self.service.add_record(
            current["id"], {"kind": "medical_follow_up",
                            "detail": "exam done", "status": "closed"},
            "hp", "health_physicist")
        closed = self.service.transition(
            current["id"], STATES[4], current["version"], "hp",
            TRANSITION_ROLES[STATES[4]][0])
        self.assertEqual(closed["status"], "closed")
        self.assertIsNone(closed["remaining_hours"])
        with self.assertRaises(ConflictError):
            self.service.add_measurement(
                current["id"], {"dose": 1, "conclusion": "normal",
                                "measured_by": "lab"},
                "recorder", "radiation_officer")

    def test_validation_and_permission(self):
        with self.assertRaises(PermissionDenied):
            self.service.add_measurement(
                self.item["id"], {"dose": 1, "conclusion": "normal",
                                  "measured_by": "lab"}, "x", "viewer")
        with self.assertRaises(ValidationError):
            self.service.add_measurement(
                self.item["id"], {"dose": -1, "conclusion": "normal",
                                  "measured_by": "lab"}, "x", "dosimetrist")
        with self.assertRaises(ValidationError):
            self.service.add_measurement(
                self.item["id"], {"dose": 1, "conclusion": "weird",
                                  "measured_by": "lab"}, "x", "dosimetrist")
        with self.assertRaises(ValidationError):
            self.service.add_measurement(
                self.item["id"],
                {"dose": 1, "conclusion": "normal", "measured_by": "lab",
                 "measured_at": "2026-09-25 08:00"},
                "x", "dosimetrist")

    def test_list_orders_by_remaining_hours(self):
        # critical事件期限4小时，low事件期限72小时，critical应排前面
        critical = self.service.create_item(
            {"title": "critical item", "description": "urgent",
             "severity": "critical", "quantity": 1, "threshold": 10,
             "external_ref": "RT-2"}, "creator", "dosimetrist")
        items = self.service.list_items("viewer")
        self.assertEqual(items[0]["id"], critical["id"])
        self.assertLessEqual(items[0]["remaining_hours"],
                             items[-1]["remaining_hours"])


if __name__ == "__main__":
    unittest.main()
