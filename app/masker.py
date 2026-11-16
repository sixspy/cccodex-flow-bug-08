from __future__ import annotations

import threading

# 遮罩提交排队锁：同一时刻只放行一个写入，后到者基于已落库记录判定冲突
_lock = threading.Lock()

STRATEGY_FULL = "全量遮"
STRATEGY_INTERNAL = "对内明文放行"
AUDIT_FULL = "全量遮罩：已加遮落库"
AUDIT_INTERNAL = "对内明文放行：明文落库已审计"
DEFAULT_RAW = "手机13800138000"


def ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS masks (
      flow_id INTEGER PRIMARY KEY,
      raw TEXT NOT NULL,
      masked TEXT NOT NULL,
      audit TEXT NOT NULL DEFAULT ''
    )""")


def apply_mask(raw: str, mode: str):
    """遮罩判定唯一入口：入库写入与详情对照都走这一套，不得各算各的。"""
    if mode == "internal":
        # 对内明文放行：落库仍是明文，但必须留下审计记录
        return raw, STRATEGY_INTERNAL, AUDIT_INTERNAL
    return "***", STRATEGY_FULL, AUDIT_FULL


def row_payload(row):
    """由入库记录推导展示对照：遮前=raw、遮后=masked，与落库数据同源。"""
    d = dict(row)
    strategy = STRATEGY_INTERNAL if d["masked"] == d["raw"] else STRATEGY_FULL
    return {
        "before": d["raw"],
        "after": d["masked"],
        "audit": d["audit"],
        "strategy_name": strategy,
        "list_label": "样条详情",
    }


def _flow_id_of(mapping, default=1):
    fid = mapping.get("flow_id")
    if isinstance(fid, (list, tuple)):
        fid = fid[0] if fid else None
    return int(fid or default)


def panel_payload(conn, args):
    ensure(conn)
    flow_id = _flow_id_of(args)
    row = conn.execute("SELECT * FROM masks WHERE flow_id=?", (flow_id,)).fetchone()
    if not row:
        return {"before": "", "after": "", "audit": "", "strategy_name": STRATEGY_FULL, "list_label": "样条详情"}
    return row_payload(row)


def mutate_action(conn, body):
    ensure(conn)
    flow_id = _flow_id_of(body)
    raw = str(body.get("raw") or DEFAULT_RAW)
    mode = body.get("mode") or "full"
    masked, _strategy, audit = apply_mask(raw, mode)
    with _lock:
        # 排队落设定：同一 flow 只保留先到的遮罩，后到冲突不覆盖，避免互相盖掉
        cur = conn.execute(
            "INSERT OR IGNORE INTO masks(flow_id, raw, masked, audit) VALUES (?,?,?,?)",
            (flow_id, raw, masked, audit),
        )
        if cur.rowcount == 0:
            row = conn.execute("SELECT * FROM masks WHERE flow_id=?", (flow_id,)).fetchone()
            out = {"ok": False, "error": "遮罩设定已存在，仅保留先到结果"}
            out.update(row_payload(row))
            return out
        conn.execute(
            "UPDATE flows SET status=?, landed=COALESCE(extracted,0) WHERE id=?",
            ("已落库", flow_id),
        )
        # 写入后再取最新落库记录回显，与设置页载入走同一套判定，不混用快照
        row = conn.execute("SELECT * FROM masks WHERE flow_id=?", (flow_id,)).fetchone()
        out = {"ok": True}
        out.update(row_payload(row))
        return out
