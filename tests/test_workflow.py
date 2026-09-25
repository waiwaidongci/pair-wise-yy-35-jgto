import tempfile, unittest
from pathlib import Path
from src.repository import Repository
from src.service import Service
from src.rules import STATES, TRANSITION_ROLES
class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.repo=Repository(str(Path(self.tmp.name)/"test.db")); self.service=Service(self.repo)
    def tearDown(self): self.repo.close(); self.tmp.cleanup()
    def test_complete_workflow_and_audit(self):
        item=self.service.create_item({"title":"workflow item","description":"complete business flow","severity":'high',"quantity":12,"threshold":6,"external_ref":"WF-1"},"creator",'dosimetrist')
        self.assertEqual(item["status"],STATES[0])
        self.service.add_record(item["id"],{"kind":"evidence","detail":"evidence registered","status":"closed","external_ref":"EV-1"},"recorder",'radiation_officer')
        # 登记复测：早先pending的检测留在历史里，最后一次exceeded结论驱动后续判定
        self.service.add_measurement(item["id"],{"measured_at":"2026-09-25T08:00:00+00:00","dose":11,"conclusion":"pending","measured_by":"lab-a"},"recorder",'radiation_officer')
        current=self.service.transition(item["id"],STATES[1],item["version"],"reviewer",TRANSITION_ROLES[STATES[1]][0])
        with self.assertRaises(Exception):
            self.service.transition(current["id"],STATES[2],current["version"],"reviewer",TRANSITION_ROLES[STATES[2]][0])
        self.service.add_measurement(item["id"],{"measured_at":"2026-09-25T10:00:00+00:00","dose":13,"conclusion":"exceeded","measured_by":"lab-b"},"recorder",'radiation_officer')
        for target in STATES[2:-1]:
            current=self.service.transition(current["id"],target,current["version"],"reviewer",TRANSITION_ROLES[target][0])
        # 医学随访未完成不能关闭
        with self.assertRaises(Exception):
            self.service.transition(current["id"],STATES[-1],current["version"],"reviewer",TRANSITION_ROLES[STATES[-1]][0])
        self.service.add_record(current["id"],{"kind":"medical_follow_up","detail":"follow up completed","status":"closed"},"hp",'health_physicist')
        current=self.service.transition(current["id"],STATES[-1],current["version"],"reviewer",TRANSITION_ROLES[STATES[-1]][0])
        self.assertEqual(current["status"],STATES[-1])
        self.assertEqual(len(self.service.list_records(current["id"],"viewer")),2)
        measurements=self.service.list_measurements(current["id"],"viewer")
        self.assertEqual([m["conclusion"] for m in measurements],["pending","exceeded"])
        events=self.service.audit("viewer",current["id"]); self.assertGreaterEqual(len(events),len(STATES)+1); self.assertTrue(self.repo.verify_audit_chain())
if __name__=="__main__": unittest.main()
