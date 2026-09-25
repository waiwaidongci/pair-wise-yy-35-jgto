from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .domain import (ConflictError, ensure_role, normalize_conclusion,
                    normalize_severity, require_iso_datetime, require_number,
                    require_text)
from .repository import Repository
from .rules import (AUDIT_ROLES, CREATE_ROLES, ENTITY, FOLLOW_UP_KIND,
                    MEASUREMENT_ROLES, RECORD_ROLES, TITLE, VIEW_ROLES,
                    completion_blockers, escalation_required, priority_score,
                    response_deadline_hours, review_blockers,
                    role_for_transition, validate_transition)


class Service:
    def __init__(self, repository: Repository):
        self.repository = repository

    def _view(self, role: str) -> None:
        ensure_role(role, VIEW_ROLES)

    @staticmethod
    def _parse_utc(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _effective(cls, item: Dict[str, Any],
                   latest: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """最后一次检测结论用于计算优先级、调查门槛和报告期限；无检测时沿用登记值。"""
        if latest is None:
            return {"dose": item["quantity"], "threshold": item["threshold"],
                    "conclusion": None}
        return {"dose": latest["dose"], "threshold": item["threshold"],
                "conclusion": latest["conclusion"]}

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

    def add_measurement(self, item_id: int, payload: Dict[str, Any],
                        actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, MEASUREMENT_ROLES)
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        if item["status"] == "closed":
            raise ConflictError("事件已关闭，不能再追加检测记录")
        measured_at = payload.get("measured_at")
        if measured_at is None:
            measured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        else:
            measured_at = require_iso_datetime(measured_at, "measured_at")
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

    def transition(self, item_id: int, target: str, expected_version: int,
                   actor: str, role: str) -> Dict[str, Any]:
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        validate_transition(item["status"], target)
        ensure_role(role, role_for_transition(target))
        if not isinstance(expected_version, int) or expected_version < 1:
            raise ValueError("expected_version必须是正整数")
        # 复测没收齐（最后一次结论仍为pending）时事件停在复核中
        blockers = review_blockers(target, self._retests_pending(item_id))
        # 医学随访未完成就不能关闭
        if not blockers:
            blockers = completion_blockers(
                target, self.repository.open_record_count(item_id),
                self.repository.follow_up_completed(item_id))
        if blockers:
            raise ConflictError("；".join(blockers))
        updated = self.repository.transition_item(item_id, target, expected_version, actor)
        latest = self.repository.latest_measurement(item_id)
        effective = self._effective(item, latest)
        self.repository.append_audit("transition", ENTITY, item_id, actor, {
            "from": item["status"], "to": target,
            "escalation_required": escalation_required(
                item["severity"], effective["dose"], effective["threshold"],
                effective["conclusion"]),
        })
        return self.enrich(updated)

    def get_item(self, item_id: int, role: str) -> Dict[str, Any]:
        self._view(role)
        return self.enrich(self.repository.get_item(item_id))

    def list_items(self, role: str, status: Optional[str] = None) -> list:
        self._view(role)
        items = [self.enrich(item) for item in self.repository.list_items(status)]
        # 列表先显示剩余时间最短的事件；已关闭事件排在最后
        return sorted(items, key=lambda item: (
            item["remaining_hours"] is None,
            item["remaining_hours"] if item["remaining_hours"] is not None else 0,
            -item["id"]))

    def list_records(self, item_id: int, role: str) -> list:
        self._view(role)
        return self.repository.list_records(item_id)

    def list_measurements(self, item_id: int, role: str) -> list:
        self._view(role)
        return self.repository.list_measurements(item_id)

    def audit(self, role: str, item_id: Optional[int] = None) -> list:
        ensure_role(role, AUDIT_ROLES)
        return self.repository.list_audit(item_id)

    def _retests_pending(self, item_id: int) -> bool:
        latest = self.repository.latest_measurement(item_id)
        return latest is None or latest["conclusion"] == "pending"

    def enrich(self, item: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(item)
        latest = self.repository.latest_measurement(item["id"])
        effective = self._effective(item, latest)
        severity = item["severity"]
        result["original_quantity"] = item["quantity"]
        result["effective_dose"] = effective["dose"]
        result["latest_conclusion"] = effective["conclusion"]
        result["retests_pending"] = (latest is None
                                     or latest["conclusion"] == "pending")
        result["follow_up_completed"] = self.repository.follow_up_completed(item["id"])
        result["priority"] = priority_score(
            severity, effective["dose"], effective["threshold"])
        result["deadline_hours"] = response_deadline_hours(
            severity, effective["dose"], effective["threshold"])
        result["escalation_required"] = escalation_required(
            severity, effective["dose"], effective["threshold"],
            effective["conclusion"])
        if item["status"] == "closed":
            result["remaining_hours"] = None
        else:
            created = self._parse_utc(item["created_at"])
            now = datetime.now(timezone.utc)
            result["remaining_hours"] = round(
                result["deadline_hours"] - (now - created).total_seconds() / 3600, 2)
        return result
