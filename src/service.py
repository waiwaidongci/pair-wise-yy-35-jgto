from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .domain import (ensure_role, normalize_conclusion, normalize_severity,
                     require_measured_at, require_number, require_text)
from .repository import Repository
from .rules import (AUDIT_ROLES, CREATE_ROLES, ENTITY, RECORD_ROLES,
                    VIEW_ROLES, completion_blockers, effective_quantity,
                    escalation_required, latest_conclusion, medical_follow_up_done,
                    priority_score, response_deadline_hours, role_for_transition,
                    validate_transition)


class Service:
    def __init__(self, repository: Repository):
        self.repository = repository

    def _view(self, role: str) -> None:
        ensure_role(role, VIEW_ROLES)

    def create_item(self, payload: Dict[str, Any], actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, CREATE_ROLES)
        actor = require_text(actor, "actor", 100)
        title = require_text(payload.get("title"), "title", 200)
        description = require_text(payload.get("description"), "description")
        severity = normalize_severity(payload.get("severity"))
        quantity = require_number(payload.get("quantity", 0), "quantity")
        threshold = require_number(payload.get("threshold", 1), "threshold", 0.000001)
        external_ref = payload.get("external_ref")
        if external_ref is not None:
            external_ref = require_text(external_ref, "external_ref", 100)
        item = self.repository.create_item(title, description, severity, quantity,
                                           threshold, external_ref, actor)
        self.repository.append_audit("create", ENTITY, item["id"], actor, {
            "title": title, "severity": severity, "quantity": quantity,
            "priority": priority_score(severity, quantity, threshold),
        })
        return self.enrich(item)

    def add_record(self, item_id: int, payload: Dict[str, Any], actor: str,
                   role: str) -> Dict[str, Any]:
        ensure_role(role, RECORD_ROLES)
        actor = require_text(actor, "actor", 100)
        kind = require_text(payload.get("kind"), "kind", 100)
        detail = require_text(payload.get("detail"), "detail")
        status = payload.get("status", "open")
        if status not in ("open", "closed"):
            raise ValueError("status必须是open或closed")
        external_ref = payload.get("external_ref")
        if external_ref is not None:
            external_ref = require_text(external_ref, "external_ref", 100)
        record = self.repository.add_record(item_id, kind, detail, status,
                                            external_ref, actor)
        self.repository.append_audit("record", ENTITY, item_id, actor, {
            "record_id": record["id"], "kind": kind, "status": status,
        })
        return record

    def add_measurement(self, item_id: int, payload: Dict[str, Any], actor: str,
                        role: str) -> Dict[str, Any]:
        """在事件下追加一次复测检测记录：检测时间、剂量、结论、检测人。

        只追加不改写，早先确认的值继续留在历史里；末次结论用于各项计算。
        """
        ensure_role(role, RECORD_ROLES)
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        if item["status"] == "closed":
            from .domain import ConflictError
            raise ConflictError("事件已关闭，不能再追加检测记录")
        measured_at = require_measured_at(payload.get("measured_at"))
        dose = require_number(payload.get("dose"), "dose")
        conclusion = normalize_conclusion(payload.get("conclusion"))
        measured_by = require_text(payload.get("measured_by"), "measured_by", 100)
        measurement = self.repository.add_measurement(
            item_id, measured_at, dose, conclusion, measured_by, actor)
        self.repository.append_audit("measurement", ENTITY, item_id, actor, {
            "measurement_id": measurement["id"], "measured_at": measured_at,
            "dose": dose, "conclusion": conclusion, "measured_by": measured_by,
        })
        return measurement

    def list_measurements(self, item_id: int, role: str) -> list:
        self._view(role)
        return self.repository.list_measurements(item_id)

    def transition(self, item_id: int, target: str, expected_version: int,
                   actor: str, role: str) -> Dict[str, Any]:
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        validate_transition(item["status"], target)
        ensure_role(role, role_for_transition(target))
        if not isinstance(expected_version, int) or expected_version < 1:
            raise ValueError("expected_version必须是正整数")
        latest = self.repository.latest_measurement(item_id)
        follow_up_done = (medical_follow_up_done(self.repository.list_records(item_id))
                          if target == "closed" else None)
        blockers = completion_blockers(
            target, self.repository.open_record_count(item_id), latest, follow_up_done)
        if blockers:
            from .domain import ConflictError
            raise ConflictError("；".join(blockers))
        updated = self.repository.transition_item(item_id, target, expected_version, actor)
        effective_dose = effective_quantity(item["quantity"], latest)
        self.repository.append_audit("transition", ENTITY, item_id, actor, {
            "from": item["status"], "to": target,
            "effective_quantity": effective_dose,
            "latest_conclusion": latest_conclusion(latest),
            "escalation_required": escalation_required(
                item["severity"], effective_dose, item["threshold"],
                latest_conclusion(latest)),
        })
        return self.enrich(updated, latest)

    def get_item(self, item_id: int, role: str) -> Dict[str, Any]:
        self._view(role)
        item = self.repository.get_item(item_id)
        return self.enrich(item, self.repository.latest_measurement(item_id))

    def list_items(self, role: str, status: Optional[str] = None) -> list:
        self._view(role)
        items = self.repository.list_items(status)
        latest_map = self.repository.latest_measurements_map([i["id"] for i in items])
        enriched = [self.enrich(item, latest_map.get(item["id"])) for item in items]
        # 列表先显示剩余时间最短的事件；已关闭的排到最后
        enriched.sort(key=lambda e: (
            e["status"] == "closed",
            e["deadline_at"] if e["status"] != "closed" else "",
            -e["id"],
        ))
        return enriched

    def list_records(self, item_id: int, role: str) -> list:
        self._view(role)
        return self.repository.list_records(item_id)

    def audit(self, role: str, item_id: Optional[int] = None) -> list:
        ensure_role(role, AUDIT_ROLES)
        return self.repository.list_audit(item_id)

    @staticmethod
    def enrich(item: Dict[str, Any], latest_measurement: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = dict(item)
        effective_dose = effective_quantity(item["quantity"], latest_measurement)
        conclusion = latest_conclusion(latest_measurement)
        result["original_quantity"] = item["quantity"]
        result["effective_quantity"] = effective_dose
        result["latest_conclusion"] = conclusion
        result["has_final_measurement"] = bool(
            latest_measurement and conclusion in ("normal", "exceeded"))
        result["priority"] = priority_score(
            item["severity"], effective_dose, item["threshold"])
        result["deadline_hours"] = response_deadline_hours(
            item["severity"], effective_dose, item["threshold"])
        deadline_at = (datetime.fromisoformat(item["created_at"])
                       + timedelta(hours=result["deadline_hours"]))
        result["deadline_at"] = deadline_at.astimezone(timezone.utc).isoformat()
        result["escalation_required"] = escalation_required(
            item["severity"], effective_dose, item["threshold"], conclusion)
        return result
