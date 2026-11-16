"""flow-bug-08 回归：遮罩落库、前后对照同源、审计、未遮不落库、并发先到保留。"""
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.db import connect
from app.masker import mutate_action


@pytest.fixture()
def auth(client):
    _req, resp = client.post("/api/login", json={"username": "admin", "password": "123456"})
    cookie = resp.cookies.get("flow_session")
    return {"Cookie": f"flow_session={cookie}"}


def _mask_row(flow_id):
    db = sqlite3.connect(os.environ["DATABASE_PATH"])
    try:
        return db.execute(
            "SELECT raw, masked, audit FROM masks WHERE flow_id=?", (flow_id,)
        ).fetchone()
    finally:
        db.close()


def test_mask_action_applies_to_storage(client, auth):
    _req, resp = client.post("/api/masker/action", json={"flow_id": 1, "mode": "full"}, headers=auth)
    assert resp.status == 200
    body = resp.json
    assert body["ok"] is True
    assert body["before"] != body["after"]
    row = _mask_row(1)
    assert row[1] == "***" and row[1] != row[0]  # 遮罩真正落在入库数据上
    _req, flow = client.get("/api/flows/1", headers=auth)
    assert flow.json["status"] == "已落库"


def test_panel_and_storage_same_judgment(client, auth):
    client.post("/api/masker/action", json={"flow_id": 1, "mode": "full"}, headers=auth)
    row = _mask_row(1)
    _req, resp = client.get("/api/masker/panel?flow_id=1", headers=auth)
    panel = resp.json
    assert panel["before"] == row[0]   # 遮前=入库明文
    assert panel["after"] == row[1]    # 遮后=入库遮罩
    assert panel["audit"] == row[2]


def test_internal_plaintext_has_audit(client, auth):
    _req, resp = client.post(
        "/api/masker/action", json={"flow_id": 2, "mode": "internal", "raw": "手机13900139000"},
        headers=auth,
    )
    assert resp.json["after"] == "手机13900139000"  # 对内明文放行
    assert resp.json["audit"]  # 审计条不得为空
    assert _mask_row(2)[2]


def test_duplicate_action_keeps_first(client, auth):
    client.post("/api/masker/action", json={"flow_id": 1, "mode": "full"}, headers=auth)
    first = _mask_row(1)
    _req, resp = client.post(
        "/api/masker/action", json={"flow_id": 1, "mode": "internal", "raw": "覆盖"}, headers=auth
    )
    assert resp.status == 409 and resp.json["ok"] is False
    assert _mask_row(1) == first  # 只保留先到结果


def test_land_requires_mask(client, auth):
    client.post("/api/flows/3/extract", headers=auth)
    _req, resp = client.post("/api/flows/3/land", headers=auth)
    assert resp.json["status"] != "已落库" and resp.json["landed"] == 0  # 未遮完不落库
    client.post("/api/masker/action", json={"flow_id": 3, "mode": "full"}, headers=auth)
    _req, resp = client.get("/api/flows/3", headers=auth)
    assert resp.json["status"] == "已落库"


def test_concurrent_actions_single_winner(client):
    db_path = os.environ["DATABASE_PATH"]

    def submit(i):
        with connect(db_path) as conn:
            try:
                out = mutate_action(conn, {"flow_id": 4, "raw": f"并发{i}", "mode": "full"})
                conn.commit()
                return out["ok"]
            except Exception:
                conn.rollback()
                raise

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(submit, range(6)))
    assert results.count(True) == 1  # 排队落设定，仅先到者成功
    assert results.count(False) == 5
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute("SELECT raw, masked FROM masks WHERE flow_id=4").fetchall()
    finally:
        db.close()
    assert len(rows) == 1 and rows[0][1] == "***"
