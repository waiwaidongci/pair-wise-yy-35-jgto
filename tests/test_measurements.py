import tempfile, unittest
from pathlib import Path
from src.repository import Repository
from src.service import Service
from src.domain import ConflictError, PermissionDenied, ValidationError
from src.rules import STATES, TRANSITION_ROLES


class MeasurementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(str(Path(self.tmp.name) / "test.db"))
        self.service = Service(self.repo)
        self.item = self.service.create_item({
            "title": "over-exposure event", "description": "retest handling",
            "severity": "high", "quantity": 12, "threshold": 10,
            "external_ref": "M-1",
        }, "creator", "dosimetrist")

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def test_measurement_fields_and_history(self):
        m1 = self.service.add_measurement(self.item["id"], {
            "measured_at": "2026-09-25T08:00:00Z", "dose": 9,
            "conclusion": "inconclusive", "measured_by": "lab-a",
        }, "officer", "radiation_officer")
        self.assertEqual(m1["conclusion"], "inconclusive")
        m2 = self.service.add_measurement(self.item["id"], {
            "measured_at": "2026-09-25T12:00:00Z", "dose": 5,
            "conclusion": "normal", "measured_by": "lab-b",
        }, "officer", "radiation_officer")
        history = self.service.list_measurements(self.item["id"], "viewer")
        # 早先确认的值继续留在历史里，不被覆盖
        self.assertEqual([m["dose"] for m in history], [9, 5])
        detail = self.service.get_item(self.item["id"], "viewer")
        self.assertEqual(detail["original_quantity"], 12)
        self.assertEqual(detail["effective_quantity"], 5)
        self.assertEqual(detail["latest_conclusion"], "normal")
        self.assertFalse(detail["escalation_required"])
        self.assertEqual(m2["id"], history[-1]["id"])

    def test_last_conclusion_drives_calculation(self):
        self.service.add_measurement(self.item["id"], {
            "measured_at": "2026-09-25T08:00:00Z", "dose": 5,
            "conclusion": "normal", "measured_by": "lab",
        }, "officer", "radiation_officer")
        normal = self.service.get_item(self.item["id"], "viewer")
        self.service.add_measurement(self.item["id"], {
            "measured_at": "2026-09-25T12:00:00Z", "dose": 30,
            "conclusion": "exceeded", "measured_by": "lab",
        }, "officer", "radiation_officer")
        exceeded = self.service.get_item(self.item["id"], "viewer")
        self.assertTrue(exceeded["escalation_required"])
        self.assertGreater(exceeded["priority"], normal["priority"])
        self.assertLessEqual(exceeded["deadline_hours"], normal["deadline_hours"])

    def test_event_stays_reviewing_until_retest_final(self):
        current = self.service.transition(
            self.item["id"], STATES[1], self.item["version"],
            "officer", TRANSITION_ROLES[STATES[1]][0])
        # 没有复测不能进入调查
        with self.assertRaises(ConflictError):
            self.service.transition(current["id"], STATES[2], current["version"],
                                    "officer", TRANSITION_ROLES[STATES[2]][0])
        self.service.add_measurement(self.item["id"], {
            "measured_at": "2026-09-25T08:00:00Z", "dose": 11,
            "conclusion": "inconclusive", "measured_by": "lab",
        }, "officer", "radiation_officer")
        current = self.service.get_item(self.item["id"], "viewer")
        # 末次结论待定，复测没收齐，仍停在复核中
        with self.assertRaises(ConflictError):
            self.service.transition(current["id"], STATES[2], current["version"],
                                    "officer", TRANSITION_ROLES[STATES[2]][0])
        self.service.add_measurement(self.item["id"], {
            "measured_at": "2026-09-25T12:00:00Z", "dose": 11,
            "conclusion": "exceeded", "measured_by": "lab",
        }, "officer", "radiation_officer")
        current = self.service.get_item(self.item["id"], "viewer")
        current = self.service.transition(current["id"], STATES[2], current["version"],
                                          "officer", TRANSITION_ROLES[STATES[2]][0])
        self.assertEqual(current["status"], "investigation")

    def test_close_requires_medical_follow_up(self):
        self.service.add_measurement(self.item["id"], {
            "measured_at": "2026-09-25T08:00:00Z", "dose": 11,
            "conclusion": "exceeded", "measured_by": "lab",
        }, "officer", "radiation_officer")
        current = self.service.get_item(self.item["id"], "viewer")
        current = self.service.transition(current["id"], STATES[1], current["version"],
                                          "officer", TRANSITION_ROLES[STATES[1]][0])
        current = self.service.transition(current["id"], STATES[2], current["version"],
                                          "officer", TRANSITION_ROLES[STATES[2]][0])
        current = self.service.transition(current["id"], STATES[3], current["version"],
                                          "physician", TRANSITION_ROLES[STATES[3]][0])
        with self.assertRaises(ConflictError):
            self.service.transition(current["id"], STATES[4], current["version"],
                                    "physician", TRANSITION_ROLES[STATES[4]][0])
        # 医学随访完成后才能关闭
        self.service.add_record(current["id"], {
            "kind": "medical_follow_up", "detail": "follow up done",
            "status": "closed", "external_ref": "FU-DONE",
        }, "physician", "health_physicist")
        current = self.service.get_item(self.item["id"], "viewer")
        # 完成随访后可以关闭
        current = self.service.transition(current["id"], STATES[4], current["version"],
                                          "physician", TRANSITION_ROLES[STATES[4]][0])
        self.assertEqual(current["status"], "closed")
        # 已关闭事件不能再追加复测
        with self.assertRaises(ConflictError):
            self.service.add_measurement(current["id"], {
                "measured_at": "2026-09-26T08:00:00Z", "dose": 1,
                "conclusion": "normal", "measured_by": "lab",
            }, "officer", "radiation_officer")

    def test_open_action_item_also_blocks_close(self):
        self.service.add_measurement(self.item["id"], {
            "measured_at": "2026-09-25T08:00:00Z", "dose": 11,
            "conclusion": "exceeded", "measured_by": "lab",
        }, "officer", "radiation_officer")
        current = self.service.get_item(self.item["id"], "viewer")
        for target in STATES[1:]:
            role = TRANSITION_ROLES[target][0]
            if target == STATES[4]:
                # 随访完成但另有未关闭事项时仍不能关闭
                self.service.add_record(current["id"], {
                    "kind": "medical_follow_up", "detail": "done",
                    "status": "closed", "external_ref": "FU-DONE",
                }, "physician", "health_physicist")
                self.service.add_record(current["id"], {
                    "kind": "action", "detail": "pending action",
                    "status": "open", "external_ref": "ACT-1",
                }, "officer", "radiation_officer")
                current = self.service.get_item(self.item["id"], "viewer")
                with self.assertRaises(ConflictError):
                    self.service.transition(current["id"], target, current["version"],
                                            "physician", role)
                break
            current = self.service.transition(current["id"], target, current["version"],
                                              "user", role)

    def test_measurement_validation_and_permissions(self):
        with self.assertRaises(PermissionDenied):
            self.service.add_measurement(self.item["id"], {
                "measured_at": "2026-09-25T08:00:00Z", "dose": 1,
                "conclusion": "normal", "measured_by": "lab",
            }, "viewer", "viewer")
        with self.assertRaises(ValidationError):
            self.service.add_measurement(self.item["id"], {
                "measured_at": "not-a-time", "dose": 1,
                "conclusion": "normal", "measured_by": "lab",
            }, "officer", "radiation_officer")
        with self.assertRaises(ValidationError):
            self.service.add_measurement(self.item["id"], {
                "measured_at": "2026-09-25T08:00:00Z", "dose": 1,
                "conclusion": "bogus", "measured_by": "lab",
            }, "officer", "radiation_officer")

    def test_list_sorted_by_remaining_time(self):
        critical = self.service.create_item({
            "title": "critical", "description": "short deadline",
            "severity": "critical", "quantity": 100, "threshold": 1,
        }, "creator", "dosimetrist")
        low = self.service.create_item({
            "title": "low", "description": "long deadline",
            "severity": "low", "quantity": 1, "threshold": 10,
        }, "creator", "dosimetrist")
        items = self.service.list_items("viewer")
        ids = [i["id"] for i in items]
        self.assertLess(ids.index(critical["id"]), ids.index(low["id"]))


if __name__ == "__main__":
    unittest.main()
