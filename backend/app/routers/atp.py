"""列车防护接口：维护防护设备，覆盖启用防护、提交升级、停用防护等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.atp import AtpService

router = APIRouter(prefix="/api/atp", tags=["列车防护"])

service = AtpService()

LIST_FIELDS = ["设备编号", "防护等级", "覆盖区段", "应答器数量", "所属线路", "版本号", "责任人", "防护状态", "备注"]
STATUSES = ["待启用", "防护正常", "版本待升级", "已停用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按设备编号检索，为空时返回全部"),
    status: str | None = Query(default=None, description="待启用、防护正常、版本待升级、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按设备编号与状态过滤列车防护列表；没有数据时返回空页，不报错。

    列表按设备编号归并了其他入口的补录数据：同一台设备只展示一条，应答器数量不会因
    补录而重复翻倍，备注为主档与各补录备注的合并。
    """
    if page < 1:
        raise HTTPException(status_code=400, detail="页码需从 1 开始")
    if size < 1 or size > 200:
        raise HTTPException(status_code=400, detail="每页条数需在 1~200 之间，请调整分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/stats")
def stats(
    keyword: str | None = Query(default=None, description="与列表一致：按设备编号过滤"),
    status: str | None = Query(default=None, description="与列表一致：按状态过滤"),
) -> dict[str, Any]:
    """顶部统计卡片：与列表共用同一份归并结果和筛选条件。

    返回在运设备、待升级版本、覆盖区段数、应答器总数（按设备去重后求和）以及没有
    应答器的设备清单与原因。
    """
    return service.stats(keyword=keyword, status=status)


@router.post("/supplements", response_model=ActionResult)
def add_supplement(payload: EntryPayload) -> ActionResult:
    """其他入口补录一条设备台账数据（按设备编号归并，不新增主档设备）。

    缺字段时不抛错，返回需要补哪些字段。
    """
    values = dict(payload.values or {})
    if payload.remark and not str(values.get("备注") or "").strip():
        values["备注"] = payload.remark
    entry, missing = service.add_supplement(values)
    if missing:
        return ActionResult(ok=False, message=f"补录信息不完整，请补充：{'、'.join(missing)}")
    return ActionResult(ok=True, message="补录数据已按设备编号归并", entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出列车防护清单：返回归并后的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "atp", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条防护设备明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"防护设备 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条防护设备，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="防护设备已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条防护设备执行启用防护、提交升级、停用防护；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
