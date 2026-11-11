"""把协商说明登记为门户预审草稿，并回读校验与本地分配账一致。

幂等：相同正文和引用重送返回已有版本，不增历史；重启后直接重跑即可。
"""
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, "/workspace")
from workbench.evidence import Journal, atomic_json  # noqa: E402

BASE = "http://127.0.0.1:8765"
OUT = Path("/workspace/reconcile/out")
JOURNAL = Path("/workspace/reconcile/state/journal.sqlite3")
DRAFT_KEY = "s1-reconcile"


def main():
    ledger = json.loads((OUT / "allocation.json").read_text(encoding="utf-8"))
    body = (OUT / "negotiation.md").read_text(encoding="utf-8")
    refs = [ledger["evidence_refs"][k] for k in
            ["L1", "L2", "L3", "P1", "P1C", "P2", "REF", "RET", "VOID", "LOC", "AU1", "AU2"]]
    title = "S1分运退回签收核对（协商说明草稿）"

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        # 读取既有版本（若有），保证版本号正确
        page.goto(f"{BASE}/drafts", wait_until="networkidle")
        exists = page.locator(f'a[href="/draft/{DRAFT_KEY}"]').count() > 0
        version = 0
        if exists:
            page.goto(f"{BASE}/draft/{DRAFT_KEY}", wait_until="networkidle")
            version = int(page.locator('input[name="version"]').get_attribute("value"))
        page.goto(f"{BASE}/draft/new" if version == 0 else f"{BASE}/draft/{DRAFT_KEY}",
                  wait_until="networkidle")
        if version == 0:
            page.fill('input[name="key"]', DRAFT_KEY)
        page.fill('input[name="title"]', title)
        page.fill('textarea[name="body"]', body)
        page.fill('textarea[name="refs"]', "\n".join(refs))
        page.click("button")
        page.wait_for_url(f"{BASE}/draft/{DRAFT_KEY}")

        # 回读保存的草稿
        saved_status = page.locator('[data-testid="draft-status"]').inner_text().strip()
        saved_version = int(page.locator('input[name="version"]').get_attribute("value"))
        saved_body = page.locator('textarea[name="body"]').input_value()
        saved_refs = page.locator('textarea[name="refs"]').input_value().splitlines()
        browser.close()

    # 一致性校验：回读草稿 vs 本地分配账
    t = ledger["totals"]
    totals_ok = all([
        re.search(rf"客户实收 \*\*{t['received']} 台\*\*", saved_body),
        re.search(rf"退回仓库 \*\*{t['returned']} 台\*\*", saved_body),
        re.search(rf"\*\*{t['unattributed']} 台未归属\*\*", saved_body),
    ])
    consistent = (
        saved_body == body
        and sorted(saved_refs) == sorted(refs)
        and set(r.split("@")[0] for r in saved_refs) == set(ledger["evidence_refs"].keys())
        and totals_ok
    )
    result = {
        "draft_key": DRAFT_KEY, "status": saved_status, "version": saved_version,
        "refs_count": len(saved_refs), "consistent_with_ledger": consistent,
    }
    atomic_json(OUT / "draft_check.json", result)
    # 进度账同键不覆盖：按草稿版本设新键，并注明与前序记录的关系
    result["supersedes"] = "draft:saved（首次校验串过严误报不一致，本次以正则复核）"
    Journal(JOURNAL).append(f"draft:verify:v{saved_version}", result)
    print(json.dumps(result, ensure_ascii=False))
    if not consistent:
        sys.exit(2)


if __name__ == "__main__":
    main()
