"""生成数量流图(mermaid)与协商说明，数据来自 allocation.json。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/workspace")
from workbench.evidence import Journal, atomic_json  # noqa: E402

OUT = Path("/workspace/reconcile/out")
JOURNAL = Path("/workspace/reconcile/state/journal.sqlite3")


def main():
    ledger = json.loads((OUT / "allocation.json").read_text(encoding="utf-8"))
    t = ledger["totals"]
    R = ledger["evidence_refs"]

    flow = f"""# S1 数量流图（发出 → 实收 / 退回 / 未归属）

```mermaid
flowchart LR
    WH["仓库发出 S1：8台<br/>{R['L1'].split('@')[0]} 主运单"]
    WH -->|"L2 东楼分运 4台 (B1,B2)<br/>{R['L2'].split('@')[0]}"| E["东楼段 4台"]
    WH -->|"L3 西楼分运 4台 (B3,B4)<br/>{R['L3'].split('@')[0]}"| W["西楼段 4台"]
    E -->|"实收 2台 DV01,DV02<br/>{R['P1C'].split('@')[0]} 补正回单·张甲授权{R['AU1'].split('@')[0]}"| ER["客户实收 2台"]
    E -->|"退回 2台 DV03,DV04 (B2)<br/>{R['RET'].split('@')[0]} 退回仓库·不计正常交付"| RT["退回仓库 2台"]
    W -->|"可分配量[0,2]：LOC现场见B3未确认<br/>{R['LOC'].split('@')[0]}"| U1["未归属 DV05,DV06 (B3)"]
    W -->|"可分配量[0,2]：无任何实物证据"| U2["未归属 DV07,DV08 (B4)"]
    P1X["P1 原签收4台：已作废<br/>{R['VOID'].split('@')[0]}·被{R['P1C'].split('@')[0]}替代·余额0"] -.-> E
    P2X["P2 西楼签收4台：李乙无西楼授权<br/>授权{R['AU2'].split('@')[0]}仅限东楼·余额0"] -.-> W
    REFX["REF 把P2引用到L1结算：同一数量的<br/>多处引用，不产生新实收"] -.-> P2X
```

合计：发出 {t['sent']} = 实收 {t['received']} + 退回 {t['returned']} + 未归属 {t['unattributed']}（余 {t['residual']}）。
未归属 4 台的可分配量均为 [0,1]/台：下界 0（无有效签收不能记实收），上界 1（可能在客户处未办手续），未知不等于丢失。
"""
    (OUT / "flow.md").write_text(flow, encoding="utf-8")

    negotiation = f"""# 协商说明：S1 分运退回后签收核对（草稿，待人工审批）

## 结论摘要
- 仓库发出 8 台（S1，4箱），经 L2 东楼、L3 西楼两段分运。
- 可确认客户实收 **2 台**（DV01、DV02，东楼，补正回单 P1C，张甲授权有效）。
- 退回仓库 **2 台**（DV03、DV04，B2 整箱，RET 关联原箱序列，不计正常交付）。
- 西楼段 **4 台未归属**（DV05–DV08）：无有效签收，不能记实收，也不能记丢失。

## 为什么不能按"总数相同"结案
承运商回单相加为 8（P1 的 4 + P2 的 4，或 P1C 2 + P2 4 + RET 2），但：
1. **P1 已作废**：VOID 自 2026-11-04 作废 P1，且 P1C 明确替代 P1（replaces），P1 可分配余额为 0；P1 与 P1C 同时计入属同一数量重复使用。
2. **P2 签字无授权**：李乙的代签授权（AU2）限定场所为**东楼**，P2 签收场所为**西楼**，授权核验不通过，P2 可分配余额为 0，不能作为客户实收。
3. **RET 是退回不是交付**：DV03、DV04 已退回仓库，计入实收属重复计数。
4. **REF 不构成新证据**：REF 把 P2 的 4 台引用到主运单 L1 结算，是同一批数量的多处引用；证据可多处引用，同一实收数量只能用一次。

## 三类问题分列
### A. 客户实际缺口（可确认）
客户有效实收 2 台，相对发出 8 台缺 6 台。其中 2 台已退回仓库（可安排补发，非损失）；剩余 4 台见 B。

### B. 承运责任未明（西楼段 4 台）
- L3 段（B3、B4 共 4 台）无任何授权签收，承运商目前不能证明完成交付。
- B3（DV05、DV06）：西楼现场记录（LOC）见过该箱 2 台，但 confirmed=false，未获确认。可分配量 [0,2]。
- B4（DV07、DV08）：无任何实物目击或交接记录。可分配量 [0,2]。
- 责任定性取决于补充证据，当前既不能记客户实收，也不能记承运丢失。

### C. 资料不足（需补材料以缩小范围）
| 缺口 | 需补材料 | 可缩小到 |
|---|---|---|
| B3 两台是否交付 | 客户授权签收人（东/西楼授权范围内）补确认，或西楼现场盘点确认（将 LOC 转为 confirmed） | 实收 2 或在途 2 |
| B4 两台去向 | 承运商西楼交接凭证/在途扫描记录，或客户收货盘点 | 实收 2 或承运责任 2 |
| P2 效力 | 客户本部对李乙西楼代签的事后授权或否认 | 决定 P2 是否转为有效 |

## 协商立场建议
- 东楼段 2 台实收、2 台退回，双方可先确认结案，不必陪争议部分搁置。
- 西楼段 4 台在承运商补证前不按交付完成结算；同时我方不主张已丢失，保留按补证结果划分责任。
- 本草稿仅为核对登记，不改变签收事实，不代替客户补签，暂不发索赔函。

## 证据引用
""" + "\n".join(f"- {R[k]}" for k in ["L1", "L2", "L3", "P1", "P1C", "P2", "REF", "RET", "VOID", "LOC", "AU1", "AU2"]) + "\n"
    (OUT / "negotiation.md").write_text(negotiation, encoding="utf-8")

    Journal(JOURNAL).append("analysis:reports", {
        "flow": "out/flow.md", "negotiation": "out/negotiation.md",
        "refs": list(R.values()),
    })
    print(json.dumps({"flow": "out/flow.md", "negotiation": "out/negotiation.md"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
