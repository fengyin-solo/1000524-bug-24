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
    keyword: str | None = Query(default=None, description="按设备编号检索"),
    status: str | None = Query(default=None, description="待启用、防护正常、版本待升级、已停用"),
    level: str | None = Query(default=None, description="按防护等级检索"),
    section: str | None = Query(default=None, description="按覆盖区段检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按设备编号、防护等级、覆盖区段与状态过滤列车防护列表；同一台设备只算一条，空条件返回全量，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, status=status, level=level, section=section, page=page, size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/summary")
def summary() -> dict[str, Any]:
    """顶部统计：应答器数量按设备去重后求和，无应答器的设备单独列出并给出原因。"""
    return service.summary()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出列车防护清单：返回当前过滤条件下的全量数据。"""
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
    entry, missing = service.create_entry(payload.values, remark=payload.remark)
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
