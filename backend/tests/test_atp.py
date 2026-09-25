"""列车防护（atp）归并口径与状态流转测试。

运行：backend 目录下 pytest
"""
from __future__ import annotations

import pytest

from app.seed import SEED_ROWS
from app.services.atp import AtpService, parse_balise_count, split_sections
from app.store import store


@pytest.fixture(autouse=True)
def reset_store():
    """每个用例都从种子数据重新开始，避免动作流转污染其他用例。"""
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    yield


service = AtpService()


def by_code(devices):
    return {row["设备编号"]: row for row in devices}


# ---- 纯函数 ----------------------------------------------------------------

def test_split_sections_dedups_and_handles_separators():
    assert split_sections("K1+000~K2+000、K2+000~K3+000") == ["K1+000~K2+000", "K2+000~K3+000"]
    assert split_sections("A，B,C；D\nE") == ["A", "B", "C", "D", "E"]
    assert split_sections("  ") == []
    assert split_sections("A、A、B") == ["A", "B"]


@pytest.mark.parametrize(
    "raw,count,reason_empty",
    [
        (12, 12, True),
        ("8", 8, True),
        (6.0, 6, True),
        (None, None, False),
        ("", None, False),
        ("abc", None, False),
        (-3, None, False),
        (True, None, False),
    ],
)
def test_parse_balise_count(raw, count, reason_empty):
    parsed, reason = parse_balise_count(raw)
    assert parsed == count
    assert (reason is None) is reason_empty
    if not reason_empty:
        assert reason


# ---- 归并与统计 -------------------------------------------------------------

def test_supplement_does_not_duplicate_device_or_double_count():
    devices = service.merged_devices()
    codes = [d["设备编号"] for d in devices]
    # ATP-0002 / ATP-0003 都有补录行，但只能出现一次
    assert codes.count("ATP-0002") == 1
    assert codes.count("ATP-0003") == 1
    assert len(devices) == 5  # 主档 4 台 + 仅补录的 ATP-0005

    rows = by_code(devices)
    # 主档应答器 12 / 16，补录又各带一份相同数字：不能翻倍
    assert rows["ATP-0002"]["应答器数量"] == 12
    assert rows["ATP-0003"]["应答器数量"] == 16
    assert rows["ATP-0002"]["应答器数量提示"] == "12（主档口径）"


def test_stats_sum_dedups_balises_and_lists_zero_and_missing_devices():
    stats = service.stats()
    assert stats["应答器总数"] == 8 + 12 + 16  # ATP-0004 为 0 不计入，ATP-0005 无数量不计入
    no_balise = {item["设备编号"]: item["原因"] for item in stats["无应答器设备"]}
    assert set(no_balise) == {"ATP-0004", "ATP-0005"}
    assert "0" in no_balise["ATP-0004"]
    assert "主档未登记" in no_balise["ATP-0005"]
    # 覆盖区段：主档 5 段 + ATP-0005 补录 1 段 = 6 段
    assert stats["覆盖区段数"] == 6
    assert stats["在运防护设备"] == 2
    assert stats["待升级版本"] == 1


def test_remark_merged_from_main_and_supplement():
    rows = by_code(service.merged_devices())
    assert "[补录]" in rows["ATP-0002"]["备注"]
    assert rows["ATP-0002"]["备注缺失"] is False
    # ATP-0001 只有主档备注
    assert rows["ATP-0001"]["备注"] == "新装设备，等待天窗点启用"
    # 两边都没备注：给提示而不是空着
    assert rows["ATP-0005"]["备注缺失"] is True
    assert "补充" in rows["ATP-0005"]["备注提示"]


def test_keyword_query_keeps_remark_and_stats_follow_filter():
    devices = service.merged_devices(keyword="ATP-0002")
    assert len(devices) == 1
    assert devices[0]["备注"]  # 按设备编号查询时备注不为空
    stats = service.stats(keyword="ATP-0002")
    assert stats["设备总数"] == 1
    assert stats["应答器总数"] == 12
    assert stats["覆盖区段数"] == 1


def test_empty_filters_return_everything_without_error():
    devices = service.merged_devices(keyword="   ", status="")
    assert len(devices) == 5
    assert service.stats(keyword=None, status=None)["覆盖区段数"] == 6


def test_list_pagination():
    items, total = service.list_entries(page=1, size=2)
    assert total == 5
    assert len(items) == 2
    items2, _ = service.list_entries(page=3, size=2)
    assert len(items2) == 1


# ---- 补录与登记 -------------------------------------------------------------

def test_add_supplement_requires_fields_and_merges():
    entry, missing = service.add_supplement({"设备编号": "ATP-0001"})
    assert entry is None
    assert missing == ["备注"]

    entry, missing = service.add_supplement(
        {"设备编号": "ATP-0001", "覆盖区段": "K12+000~K13+000", "应答器数量": 8, "备注": "二次核对"}
    )
    assert missing == [] and entry is not None
    # 补录后仍只有一台 ATP-0001，数量仍取主档口径
    rows = by_code(service.merged_devices())
    assert list(rows).count("ATP-0001") == 1
    assert rows["ATP-0001"]["应答器数量"] == 8
    assert "二次核对" in rows["ATP-0001"]["备注"]


def test_add_supplement_rejects_bad_count():
    _, missing = service.add_supplement({"设备编号": "ATP-0001", "应答器数量": "十二台", "备注": "x"})
    assert missing and "格式有误" in missing[0]


def test_supplement_for_new_device_appears_as_unregistered():
    service.add_supplement({"设备编号": "ATP-0009", "覆盖区段": "S1、S2", "备注": "新线补录"})
    rows = by_code(service.merged_devices())
    assert "ATP-0009" in rows
    assert rows["ATP-0009"]["主档已登记"] is False
    assert rows["ATP-0009"]["可操作"] is False
    assert rows["ATP-0009"]["防护状态"] == "主档未登记"
    assert service.stats()["覆盖区段数"] == 8  # 6 + S1 + S2


def test_create_entry_rejects_duplicate_code():
    entry, missing = service.create_entry(
        {"设备编号": "ATP-0001", "防护等级": "CTCS-2", "覆盖区段": "X"}
    )
    assert entry is None
    assert "已在主档登记" in missing[0]


def test_create_entry_reports_missing_fields():
    entry, missing = service.create_entry({"设备编号": "ATP-0100"})
    assert entry is None
    assert missing == ["防护等级", "覆盖区段"]


def test_create_entry_normalizes_and_validates_count():
    entry, missing = service.create_entry(
        {"设备编号": "ATP-0100", "防护等级": "CTCS-2", "覆盖区段": "S9", "应答器数量": "6"}
    )
    assert missing == [] and entry["应答器数量"] == 6

    entry, missing = service.create_entry(
        {"设备编号": "ATP-0101", "防护等级": "CTCS-2", "覆盖区段": "S9", "应答器数量": "六台"}
    )
    assert entry is None and "格式有误" in missing[0]


# ---- 状态流转 ---------------------------------------------------------------

def test_upgrade_keeps_stats_consistent_with_list():
    # ATP-0003 提交升级后：覆盖区段仍是主档 2 段，与列表归并结果一致
    service.run_action(3, "提交升级")
    rows = by_code(service.merged_devices(status="版本待升级"))
    assert "ATP-0003" in rows
    assert split_sections(rows["ATP-0003"]["覆盖区段"]) == ["K14+200~K15+400", "K15+400~K16+000"]
    stats = service.stats(status="版本待升级")
    assert stats["覆盖区段数"] == 2
    assert stats["应答器总数"] == 16
    assert stats["待升级版本"] == 1


def test_enable_and_disable_actions_unchanged():
    entry, message = service.run_action(1, "启用防护")
    assert entry["status"] == "防护正常" and entry["abnormal"] is False
    assert message == "防护设备已启用防护"

    entry, message = service.run_action(1, "停用防护")
    assert entry["status"] == "已停用" and entry["abnormal"] is True
    assert entry["pending"] is False
    assert message == "防护设备已停用防护"


def test_action_blocked_for_unknown_and_unregistered_device():
    entry, message = service.run_action(999, "启用防护")
    assert entry is None and "不存在" in message

    entry, message = service.run_action(5, "启用防护")  # ATP-0005 仅补录，主档 id 不存在
    assert entry is None and "主档" in message
