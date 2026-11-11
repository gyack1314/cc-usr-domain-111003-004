"""数量流分配与验证：从发运到实收/退回/未归属，生成机器可读分配账。

规则（materials/policy.md）：
- 撤销凭证没有可分配余额；补正只替代明确凭证
- 签收必须有客户本部授权，代签授权限定场所和起止日
- 退回需关联原箱或实物序列，不计正常交付
- 同一实物序列不得多次实收；证据可多处引用，同一实收数量只能用一次
- 证据不足时对可分配量给上下界，未知不等于丢失
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, "/workspace")
from workbench.domain import authorized, balance, overused  # noqa: E402
from workbench.evidence import Journal, atomic_json  # noqa: E402

ROOT = Path("/workspace")
OUT = ROOT / "reconcile/out"
JOURNAL = ROOT / "reconcile/state/journal.sqlite3"


def load_inputs():
    collected = json.loads((OUT / "collected.json").read_text(encoding="utf-8"))
    fields = {rid: rec["fields"] for rid, rec in collected.items()}
    refs = {rid: rec["reference"] for rid, rec in collected.items()}
    with open(ROOT / "materials/dispatch.csv", encoding="utf-8") as f:
        dispatch = list(csv.DictReader(f))
    with open(ROOT / "materials/packing.csv", encoding="utf-8") as f:
        packing = list(csv.DictReader(f))
    box_of = {row["serial"]: row["box"] for row in packing}
    serials_of = {row["box"]: row["serials"].split("|") for row in dispatch}
    return collected, fields, refs, dispatch, box_of, serials_of


def main():
    collected, F, REFS, dispatch, box_of, serials_of = load_inputs()
    journal = Journal(JOURNAL)

    # ---- 1. 凭证效力：作废与补正（只按明确替代关系） ----
    voided = {F["VOID"]["target"]: F["VOID"]["effective"]}  # P1 自 2026-11-04 作废
    replaced_by = {}
    for rid, f in F.items():
        if "replaces" in f:
            replaced_by[f["replaces"]] = rid  # P1 -> P1C
    receipts = {r: f for r, f in F.items() if "signer" in f and "leg" in f}
    capacity = {}
    receipt_status = {}
    for rid, f in receipts.items():
        if rid in voided:
            capacity[rid] = 0
            receipt_status[rid] = f"已作废(VOID, {voided[rid]}生效)" + (
                f"，由{replaced_by[rid]}补正替代" if rid in replaced_by else ""
            )
        else:
            capacity[rid] = f["qty"]
            receipt_status[rid] = "有效凭证"
    # ---- 2. 签字授权核验 ----
    grants = [{"id": g, **F[g]} for g in ("AU1", "AU2")]
    auth_result = {}
    for rid, f in receipts.items():
        ok = any(
            authorized(g, f["signer"], f["site"], f["date"]) for g in grants
        )
        auth_result[rid] = ok
        if not ok:
            capacity[rid] = 0  # 无授权签收不产生客户实收余额
            receipt_status[rid] += "；签字人未获该场所授权，不计客户实收"

    # ---- 3. 分配：实收、退回、未归属 ----
    allocations = []  # 机器可读分配账
    serial_state = {}
    # 实收：仅来自有效且有授权的凭证
    for rid, f in sorted(receipts.items()):
        if capacity[rid] > 0 and auth_result[rid]:
            for s in f["serials"]:
                allocations.append({
                    "serial": s, "box": box_of[s], "dest": "客户实收",
                    "site": f["site"], "source": REFS[rid], "qty": 1,
                })
                serial_state[s] = ("客户实收", rid)
    # 退回：关联原箱/序列，不计正常交付
    ret = F["RET"]
    for s in ret["serials"]:
        assert s not in serial_state, f"{s} 重复实收"
        allocations.append({
            "serial": s, "box": ret["box"], "dest": "退回仓库",
            "site": "仓库", "source": REFS["RET"], "qty": 1,
        })
        serial_state[s] = ("退回仓库", "RET")
    # 未归属：发出但无有效实收、无退回记录的序列，给上下界
    all_serials = sorted(box_of)
    unattributed = [s for s in all_serials if s not in serial_state]
    loc = F["LOC"]
    bounds = {}
    for s in unattributed:
        box = box_of[s]
        sighted = box in loc["boxes_seen"] and not loc["confirmed"]
        ev = [REFS["L3"]]
        note = "西楼分运段，无有效签收"
        if sighted:
            ev.append(REFS["LOC"])
            note += "；现场记录见过该箱但未确认(confirmed=false)"
        else:
            note += "；无任何实物目击记录"
        bounds[s] = {"lower": 0, "upper": 1, "evidence": ev, "note": note}
        allocations.append({
            "serial": s, "box": box, "dest": "未归属", "site": "西楼段",
            "source": ev, "qty": 1, "allocatable_bound": [0, 1], "note": note,
        })
        serial_state[s] = ("未归属", None)

    # ---- 4. 总量核算（领域工具） ----
    sent = sum(int(r["qty"]) for r in dispatch)
    received = sum(a["qty"] for a in allocations if a["dest"] == "客户实收")
    returned = sum(a["qty"] for a in allocations if a["dest"] == "退回仓库")
    in_transit = sum(a["qty"] for a in allocations if a["dest"] == "未归属")
    residual = balance(sent, received, returned, in_transit)

    # ---- 5. 验证 ----
    checks = []
    # 5a 每台设备都有去向
    missing = [s for s in all_serials if s not in serial_state]
    checks.append({"check": "每台设备均有去向", "ok": not missing, "detail": missing or "8/8 已登记"})
    # 5b 凭证未超额使用
    alloc_by_receipt = [
        {"receipt": a["source"].split("@")[0], "qty": a["qty"]}
        for a in allocations if a["dest"] == "客户实收"
    ]
    over = overused(alloc_by_receipt, capacity)
    checks.append({"check": "凭证未超额使用", "ok": not over, "detail": over or "各回单分配量均未超可用余额"})
    # 5c 退回未计入正常签收
    ret_in_receipts = any(
        s in ret["serials"] for f in receipts.values() if capacity.get(next(r for r, x in receipts.items() if x is f), 0) > 0
        for s in f["serials"]
    )
    checks.append({"check": "退回未计入正常签收", "ok": not ret_in_receipts,
                   "detail": "RET序列DV03/DV04未出现在任何有效签收分配中"})
    # 5d 总量平衡
    checks.append({"check": "总量平衡", "ok": residual == 0,
                   "detail": f"发出{sent}=实收{received}+退回{returned}+未归属{in_transit}，余{residual}"})
    # 5e 同一序列不重复实收
    recv_serials = [a["serial"] for a in allocations if a["dest"] == "客户实收"]
    checks.append({"check": "同一序列不重复实收", "ok": len(recv_serials) == len(set(recv_serials)),
                   "detail": sorted(recv_serials)})
    # 5f 承运商"总数相同"算法核验：P1(4)+P2(4)=8 或 P1C(2)+P2(4)+RET(2)=8
    carrier_over = overused(
        [{"receipt": "P1", "qty": 4}, {"receipt": "P2", "qty": 4}], capacity)
    checks.append({"check": "承运商结账算法核验", "ok": bool(carrier_over),
                   "detail": f"按可用余额核算，承运商引用的凭证超额：{carrier_over}（P1已作废余额0，P2无授权余额0）"})

    verification = {"checks": checks, "all_ok": all(c["ok"] for c in checks)}

    # ---- 6. 输出分配账 ----
    ledger = {
        "shipment": "S1", "generated_from": "portal+materials",
        "capacities": capacity, "receipt_status": receipt_status,
        "authorization": auth_result,
        "totals": {"sent": sent, "received": received, "returned": returned,
                   "unattributed": in_transit, "residual": residual},
        "allocations": allocations,
        "unattributed_bounds": bounds,
        "cross_references": [{
            "id": REFS["REF"],
            "note": "REF将P2(qty4)引用到主运单L1结算；属同一批数量的多处引用，不构成新的实收，P2可用余额为0",
        }],
        "evidence_refs": REFS,
    }
    atomic_json(OUT / "allocation.json", ledger)
    atomic_json(OUT / "verification.json", verification)
    journal.append("analysis:allocation", {
        "totals": ledger["totals"], "capacities": capacity,
        "authorization": auth_result, "all_ok": verification["all_ok"],
    })
    print(json.dumps({"totals": ledger["totals"], "all_ok": verification["all_ok"],
                      "checks": len(checks)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
