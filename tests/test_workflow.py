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
        # 首次复测结论待定，事件必须停在复核中
        self.service.add_measurement(item["id"],{"measured_at":"2026-09-25T08:00:00Z","dose":12,"conclusion":"inconclusive","measured_by":"dosimetry-lab"},"recorder",'radiation_officer')
        current=self.service.transition(item["id"],STATES[1],item["version"],"reviewer",TRANSITION_ROLES[STATES[1]][0])
        with self.assertRaises(Exception):
            self.service.transition(current["id"],STATES[2],current["version"],"reviewer",TRANSITION_ROLES[STATES[2]][0])
        # 末次复测给出定论后才能进入调查
        self.service.add_measurement(item["id"],{"measured_at":"2026-09-25T10:00:00Z","dose":13,"conclusion":"exceeded","measured_by":"dosimetry-lab"},"recorder",'radiation_officer')
        current=self.service.get_item(item["id"],"viewer")
        for target in STATES[2:-1]:
            current=self.service.transition(current["id"],target,current["version"],"reviewer",TRANSITION_ROLES[target][0])
        # 医学随访未完成不能关闭
        with self.assertRaises(Exception):
            self.service.transition(current["id"],STATES[-1],current["version"],"reviewer",TRANSITION_ROLES[STATES[-1]][0])
        self.service.add_record(current["id"],{"kind":"medical_follow_up","detail":"medical follow up completed","status":"closed","external_ref":"FU-1"},"physician",'health_physicist')
        current=self.service.get_item(item["id"],"viewer")
        current=self.service.transition(current["id"],STATES[-1],current["version"],"reviewer",TRANSITION_ROLES[STATES[-1]][0])
        self.assertEqual(current["status"],STATES[-1])
        self.assertEqual(len(self.service.list_records(current["id"],"viewer")),2)
        self.assertEqual(len(self.service.list_measurements(current["id"],"viewer")),2)
        events=self.service.audit("viewer",current["id"]); self.assertGreaterEqual(len(events),len(STATES)+3); self.assertTrue(self.repo.verify_audit_chain())
if __name__=="__main__": unittest.main()
