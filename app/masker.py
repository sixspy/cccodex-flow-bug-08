from __future__ import annotations

def ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS masks (
      flow_id INTEGER PRIMARY KEY,
      raw TEXT NOT NULL,
      masked TEXT NOT NULL,
      audit TEXT NOT NULL DEFAULT ''
    )""")

def panel_payload(conn, args):
    ensure(conn)
    flow_id = int(args.get("flow_id") or 1)
    row = conn.execute("SELECT * FROM masks WHERE flow_id=?", (flow_id,)).fetchone()
    if not row:
        return {"before": "", "after": "", "audit": "", "strategy_name": "全量遮", "list_label": "样条详情"}
    d = dict(row)
    return {
        "before": d["masked"],
        "after": d["masked"],
        "audit": d["audit"],
        "strategy_name": "全量遮",
        "list_label": "样条详情",
    }

def mutate_action(conn, body):
    ensure(conn)
    flow_id = int(body.get("flow_id") or 1)
    raw = str(body.get("raw") or "手机13800138000")
    mode = body.get("mode") or "full"
    masked = "***"
    audit = ""
    if mode == "internal":
        masked = raw
        audit = ""
    conn.execute(
        "INSERT OR REPLACE INTO masks(flow_id, raw, masked, audit) VALUES (?,?,?,?)",
        (flow_id, raw, masked, audit),
    )
    conn.execute(
        "UPDATE flows SET status=?, landed=COALESCE(extracted,0) WHERE id=?",
        ("已落库", flow_id),
    )
    # 响应层再遮一次造成假正常
    return {"ok": True, "before": masked, "after": masked, "audit": audit}
