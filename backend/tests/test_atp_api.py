"""列车防护接口冒烟测试：列表、统计、补录、动作用同一份内存数据走一遍。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.seed import SEED_ROWS
from app.store import store


@pytest.fixture(autouse=True)
def reset_store():
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    yield


client = TestClient(app)


def test_list_and_stats_agree_with_empty_query():
    response = client.get("/api/atp")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5

    stats = client.get("/api/atp/stats").json()
    assert stats["覆盖区段数"] == 6
    assert stats["应答器总数"] == 36
    # 列表覆盖区段拆开去重后的数量与顶部统计一致
    from app.services.atp import split_sections

    listed_sections = {
        section
        for item in data["items"]
        for section in split_sections(item["覆盖区段"])
    }
    assert len(listed_sections) == stats["覆盖区段数"]


def test_keyword_query_contains_remark():
    item = client.get("/api/atp", params={"keyword": "ATP-0002"}).json()["items"][0]
    assert item["备注"]
    assert "工区台账" in item["备注"]


def test_stats_filtered_like_list():
    stats = client.get("/api/atp/stats", params={"status": "版本待升级"}).json()
    assert stats["设备总数"] == 1
    assert stats["覆盖区段数"] == 2


def test_supplement_missing_fields_does_not_500():
    response = client.post("/api/atp/supplements", json={"values": {"设备编号": "ATP-0001"}})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert "备注" in payload["message"]


def test_supplement_bad_count_reports_reason():
    response = client.post(
        "/api/atp/supplements",
        json={"values": {"设备编号": "ATP-0001", "应答器数量": "八台", "备注": "核对"}},
    )
    payload = response.json()
    assert payload["ok"] is False
    assert "格式有误" in payload["message"]


def test_supplement_ok_merges_into_device():
    response = client.post(
        "/api/atp/supplements",
        json={"values": {"设备编号": "ATP-0001", "覆盖区段": "K12+500~K12+800", "备注": "补一段"}},
    )
    assert response.json()["ok"] is True
    item = client.get("/api/atp", params={"keyword": "ATP-0001"}).json()["items"][0]
    assert "K12+500~K12+800" in item["覆盖区段"]
    assert "补一段" in item["备注"]


def test_invalid_pagination_is_400():
    assert client.get("/api/atp", params={"size": 500}).status_code == 400
    assert client.get("/api/atp", params={"page": 0}).status_code == 400


def test_old_actions_still_work_then_stats_still_match():
    assert client.post("/api/atp/1/actions", json={"values": {"action": "启用防护"}}).json()["ok"]
    assert client.post("/api/atp/2/actions", json={"values": {"action": "停用防护"}}).json()["ok"]
    stats = client.get("/api/atp/stats").json()
    assert stats["在运防护设备"] == 2  # ATP-0001 新启用 + ATP-0004
    listed = client.get("/api/atp", params={"status": "已停用"}).json()
    assert listed["total"] == 1 and listed["items"][0]["设备编号"] == "ATP-0002"


def test_export_returns_merged_rows():
    payload = client.get("/api/atp/export").json()
    assert payload["total"] == 5
    codes = [item["设备编号"] for item in payload["items"]]
    assert len(codes) == len(set(codes))
