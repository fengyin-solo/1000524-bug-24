"""列车防护业务规则：状态流转、字段校验、归并口径与统计都收在这里。

数据有两个来源：
- ``atp``：防护设备主档，一台设备一条；
- ``atp_supplement``：工区台账、调度补录等「其他入口」追加的数据，同一设备编号可能有
  多条。直接把两边行数相加会把同一台设备重复计数（应答器数量翻倍、没有应答器的补录
  设备也被算成有），所以列表与统计一律先按「设备编号」归并成一台设备再使用。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "atp"
SUPPLEMENT_MODULE = "atp_supplement"
REQUIRED_FIELDS = ["设备编号", "防护等级", "覆盖区段"]
SUPPLEMENT_REQUIRED_FIELDS = ["设备编号", "备注"]
STATUS_ORDER = ["待启用", "防护正常", "版本待升级", "已停用"]
ACTION_RULES = {"启用防护": "防护正常", "提交升级": "版本待升级", "停用防护": "已停用"}
NEGATIVE_ACTIONS = ["停用防护"]

# 覆盖区段字段里一个区段串可能写了多个区段，用这些分隔符拆开后再去重。
SECTION_SPLITTERS = ("、", "，", ",", ";", "；", "\n")


def _clean(value: Any) -> str:
    return str(value if value is not None else "").strip()


def split_sections(raw: Any) -> list[str]:
    """把覆盖区段字段拆成区段清单，去掉空白与重复项，保持出现顺序。"""
    text = _clean(raw)
    if not text:
        return []
    parts = [text]
    for splitter in SECTION_SPLITTERS:
        parts = [piece for part in parts for piece in part.split(splitter)]
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        name = part.strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def parse_balise_count(raw: Any) -> tuple[int | None, str | None]:
    """解析应答器数量。

    返回 ``(数量, 异常原因)``：能解析为非负整数时原因为空；缺失、为负或格式无法解析时，
    数量为 ``None`` 并给出原因，调用方据此把「没有应答器 / 数量不可信」的设备单独标出，
    绝不把它当成 0 混进求和里。
    """
    if raw is None or not _clean(raw):
        return None, "应答器数量未填写"
    if isinstance(raw, bool):
        return None, f"应答器数量格式有误：{raw!r}"
    if isinstance(raw, int):
        return (raw, None) if raw >= 0 else (None, f"应答器数量不能为负数：{raw}")
    if isinstance(raw, float) and raw.is_integer():
        value = int(raw)
        return (value, None) if value >= 0 else (None, f"应答器数量不能为负数：{value}")
    text = _clean(raw)
    if text.lstrip("-").isdigit():
        value = int(text)
        return (value, None) if value >= 0 else (None, f"应答器数量不能为负数：{text}")
    return None, f"应答器数量格式有误：{text}"


class AtpService:
    # ---- 归并口径 ----------------------------------------------------------

    def _supplement_groups(self) -> dict[str, list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in store.rows(SUPPLEMENT_MODULE):
            code = _clean(row.get("设备编号"))
            if code:
                groups.setdefault(code, []).append(row)
        return groups

    def merged_devices(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """把主档与其他入口补录按设备编号归并成「一台设备一条」。

        应答器数量只取主档一个口径，补录行再带多少份都不会翻倍；只在补录里出现的设备
        视为主档未登记的设备，单独标出并说明原因。
        """
        groups = self._supplement_groups()
        devices: list[dict[str, Any]] = []
        seen_codes: set[str] = set()

        for row in store.rows(MODULE):
            code = _clean(row.get("设备编号"))
            seen_codes.add(code)
            devices.append(self._merge_row(row, groups.get(code, []), registered=True))

        # 只有补录、主档里查不到的设备编号：补一台“主档未登记”的视图记录。
        for code, extras in groups.items():
            if code not in seen_codes:
                devices.append(self._merge_row({}, extras, registered=False))

        keyword = _clean(keyword)
        if keyword:
            devices = [device for device in devices if keyword in _clean(device.get("设备编号"))]
        status = _clean(status)
        if status:
            devices = [device for device in devices if _clean(device.get("status")) == status]
        return devices

    def _merge_row(
        self,
        row: dict[str, Any],
        extras: list[dict[str, Any]],
        *,
        registered: bool,
    ) -> dict[str, Any]:
        device = dict(row)
        device["id"] = row.get("id")
        device["设备编号"] = _clean(row.get("设备编号")) or _clean(
            extras[0].get("设备编号") if extras else ""
        )
        device["数据来源"] = "主档" if registered else "其他入口补录（主档未登记）"
        device["主档已登记"] = registered
        device["可操作"] = registered

        # 覆盖区段：主档与补录区段合并去重，升级新增的区段在这里就与列表对齐。
        sections = split_sections(row.get("覆盖区段"))
        for extra in extras:
            for name in split_sections(extra.get("覆盖区段")):
                if name not in sections:
                    sections.append(name)
        device["覆盖区段"] = "、".join(sections)

        # 应答器数量以主档为准；主档缺失时才回退到补录，且只取一个口径，不做跨行求和。
        count_raw = row.get("应答器数量")
        count_source = "主档"
        if registered and (count_raw is None or not _clean(count_raw)):
            for extra in extras:
                if extra.get("应答器数量") is not None and _clean(extra.get("应答器数量")):
                    count_raw = extra.get("应答器数量")
                    count_source = "其他入口补录"
                    break
        if not registered:
            # 补录设备没有主档口径，数量一律视为待核对，避免被补录数字直接当成在运统计。
            count_raw = None
            count_source = "其他入口补录"

        count, reason = parse_balise_count(count_raw)
        device["应答器数量"] = count
        if count == 0:
            reason = "应答器数量为 0，现场未配置应答器"
        device["应答器数量异常"] = reason is not None
        if registered:
            device["无应答器原因"] = reason or ""
        else:
            device["无应答器原因"] = (
                f"主档未登记该设备，应答器数量待核对（{reason or '补录未提供数量'}）"
            )

        if reason is None and count is not None:
            device["应答器数量提示"] = f"{count}（{count_source}口径）"
        else:
            device["应答器数量提示"] = device["无应答器原因"]

        # 备注：主档备注 + 各补录入口备注合并；都没有时给占位提示，而不是空着让人误判。
        remarks: list[str] = []
        main_remark = _clean(row.get("备注"))
        if main_remark:
            remarks.append(main_remark)
        for extra in extras:
            text = _clean(extra.get("备注"))
            if text and text not in remarks:
                remarks.append(f"[补录] {text}")
        device["备注"] = "；".join(remarks)
        device["备注缺失"] = not remarks
        device["备注提示"] = "；".join(remarks) if remarks else "暂无备注，请补充台账说明"

        if not registered:
            device["status"] = STATUS_ORDER[0]
            device["防护状态"] = "主档未登记"
            device["pending"] = True
            device["abnormal"] = False
        else:
            device.setdefault("防护状态", _clean(row.get("status")))
        return device

    # ---- 列表与统计 --------------------------------------------------------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        devices = self.merged_devices(keyword=keyword, status=status)
        total = len(devices)
        start = max(page - 1, 0) * size
        return devices[start:start + size], total

    def stats(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """顶部统计：与列表同一份归并结果、同一套筛选条件，保证两边对得上。

        - 应答器总数按设备去重后求和（每台设备只计一个口径）；
        - 覆盖区段数取归并后所有非空区段的去重数；
        - 没有应答器（数量缺失/不可信/主档未登记）的设备单独列清单并给原因。
        """
        devices = self.merged_devices(keyword=keyword, status=status)
        section_set: set[str] = set()
        balise_total = 0
        no_balise: list[dict[str, str]] = []
        for device in devices:
            section_set.update(split_sections(device.get("覆盖区段")))
            count = device.get("应答器数量")
            if isinstance(count, int) and count > 0:
                balise_total += count
            else:
                no_balise.append({
                    "设备编号": _clean(device.get("设备编号")),
                    "原因": _clean(device.get("无应答器原因")) or "应答器数量未填写",
                })
        return {
            "设备总数": len(devices),
            "在运防护设备": sum(1 for d in devices if _clean(d.get("status")) == "防护正常"),
            "待升级版本": sum(1 for d in devices if _clean(d.get("status")) == "版本待升级"),
            "覆盖区段数": len(section_set),
            "应答器总数": balise_total,
            "无应答器设备": no_balise,
            "覆盖区段清单": sorted(section_set),
        }

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        for device in self.merged_devices():
            if device.get("id") == entry_id:
                return device
        return None

    # ---- 写操作 ------------------------------------------------------------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not _clean(values.get(field))]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        code = _clean(values.get("设备编号"))
        duplicate = next((row for row in rows if _clean(row.get("设备编号")) == code), None)
        if duplicate is not None:
            return None, [f"设备编号 {code} 已在主档登记，请勿重复登记，可走补录入口补充信息"]
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: _clean(values.get(field)) for field in REQUIRED_FIELDS})
        count, reason = parse_balise_count(values.get("应答器数量"))
        provided = values.get("应答器数量") is not None and _clean(values.get("应答器数量"))
        if provided and reason:
            return None, [reason]
        entry["应答器数量"] = count if provided else None
        for optional in ("所属线路", "版本号", "责任人", "防护状态", "备注"):
            entry[optional] = _clean(values.get(optional))
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def add_supplement(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """其他入口补录：按设备编号追加一条台账数据，不新增主档设备。"""
        missing = [field for field in SUPPLEMENT_REQUIRED_FIELDS if not _clean(values.get(field))]
        if missing:
            return None, missing
        if values.get("应答器数量") is not None and _clean(values.get("应答器数量")):
            _, reason = parse_balise_count(values.get("应答器数量"))
            if reason:
                return None, [reason]
        rows = store.rows(SUPPLEMENT_MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry["设备编号"] = _clean(values.get("设备编号"))
        entry["覆盖区段"] = _clean(values.get("覆盖区段"))
        entry["应答器数量"] = values.get("应答器数量")
        entry["备注"] = _clean(values.get("备注"))
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"防护设备 {entry_id} 不存在或已归档（其他入口补录的设备需先登记主档）"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于列车防护可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"防护设备已{action}"
