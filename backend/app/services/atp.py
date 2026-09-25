"""列车防护业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

MODULE = "atp"
REQUIRED_FIELDS = ["设备编号", "防护等级", "覆盖区段"]
OPTIONAL_FIELDS = ["应答器数量", "所属线路", "版本号", "责任人", "防护状态", "备注"]
EXPECTED_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS
STATUS_ORDER = ["待启用", "防护正常", "版本待升级", "已停用"]
ACTION_RULES = {"启用防护": "防护正常", "提交升级": "版本待升级", "停用防护": "已停用"}
NEGATIVE_ACTIONS = ["停用防护"]

_SECTION_SPLIT = re.compile(r"[、,，;；/\s]+")


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _device_key(row: dict[str, Any]) -> str:
    return str(row.get("设备编号") or "").strip()


def _balise_count(row: dict[str, Any]) -> int | None:
    """应答器数量统一成整数；空值、非数字都按未登记处理。"""
    raw = row.get("应答器数量")
    if _is_blank(raw):
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def _split_sections(value: Any) -> list[str]:
    """覆盖区段允许用顿号、逗号、分号分隔，拆成去重后的区段列表。"""
    parts = [part for part in _SECTION_SPLIT.split(str(value or "").strip()) if part]
    return list(dict.fromkeys(parts))


class AtpService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        level: str | None = None,
        section: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        keyword = (keyword or "").strip()
        level = (level or "").strip()
        section = (section or "").strip()
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("设备编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if level:
            rows = [row for row in rows if level in str(row.get("防护等级", ""))]
        if section:
            rows = [row for row in rows if section in str(row.get("覆盖区段", ""))]
        devices = [self._annotate(row) for row in self._dedupe(rows)]
        total = len(devices)
        start = max(page - 1, 0) * size
        return devices[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._annotate(row) if row is not None else None

    def summary(self) -> dict[str, Any]:
        """顶部统计口径：应答器数量按设备去重后求和，无应答器的设备单独列出原因。"""
        devices = self._dedupe(store.rows(MODULE))
        sections: list[str] = []
        balise_total = 0
        no_balise: list[dict[str, Any]] = []
        for device in devices:
            sections.extend(_split_sections(device.get("覆盖区段")))
            count = _balise_count(device)
            if count:
                balise_total += count
            else:
                no_balise.append({
                    "设备编号": device.get("设备编号"),
                    "原因": self._no_balise_reason(device),
                })
        return {
            "在运防护设备": sum(1 for device in devices if device.get("status") == "防护正常"),
            "待升级版本": sum(1 for device in devices if device.get("status") == "版本待升级"),
            "覆盖区段数": len(set(sections)),
            "应答器总数": balise_total,
            "无应答器设备": no_balise,
        }

    def create_entry(self, values: dict[str, Any], remark: str | None = None) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if _is_blank(values.get(field))]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in EXPECTED_FIELDS:
            value = values.get(field)
            if not _is_blank(value):
                entry[field] = value
        if _is_blank(entry.get("备注")) and not _is_blank(remark):
            entry["备注"] = remark
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"防护设备 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于列车防护可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        key = _device_key(entry)
        for row in store.rows(MODULE):
            if _device_key(row) == key:
                row["status"] = target
                row["pending"] = target != STATUS_ORDER[-1]
                row["abnormal"] = action in NEGATIVE_ACTIONS
        return self._annotate(entry), f"防护设备已{action}"

    def _dedupe(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """同一台设备（设备编号相同）只算一条：其他入口补录的记录按字段合并，
        应答器数量取各记录最大值，覆盖区段取并集，避免统计时被重复计数。"""
        devices: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for row in rows:
            key = _device_key(row) or f"__未编号_{row.get('id', len(order))}"
            if key not in devices:
                devices[key] = dict(row)
                devices[key]["重复记录数"] = 1
                order.append(key)
                continue
            merged = devices[key]
            merged["重复记录数"] = int(merged.get("重复记录数", 1)) + 1
            for field, value in row.items():
                if field in ("id", "重复记录数"):
                    continue
                if _is_blank(merged.get(field)) and not _is_blank(value):
                    merged[field] = value
            counts = [count for count in (_balise_count(merged), _balise_count(row)) if count is not None]
            if counts:
                merged["应答器数量"] = max(counts)
            sections = _split_sections(merged.get("覆盖区段")) + _split_sections(row.get("覆盖区段"))
            merged["覆盖区段"] = "、".join(dict.fromkeys(sections))
        return [devices[key] for key in order]

    def _annotate(self, row: dict[str, Any]) -> dict[str, Any]:
        """补两个展示字段：待补字段（还空着哪些字段）与无应答器原因。"""
        item = dict(row)
        item["待补字段"] = [field for field in EXPECTED_FIELDS if _is_blank(item.get(field))]
        count = _balise_count(item)
        item["无应答器原因"] = None if count else self._no_balise_reason(item)
        return item

    def _no_balise_reason(self, row: dict[str, Any]) -> str:
        count = _balise_count(row)
        reason = "应答器数量登记为 0" if count == 0 else "未登记应答器数量"
        remark = str(row.get("备注") or "").strip()
        if remark:
            reason += f"，备注：{remark}"
        else:
            reason += "，备注缺失需补充"
        return reason
