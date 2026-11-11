"""通过门户页面采集证据：列表分页 -> 详情页 -> 附件与文书下载。

幂等：已下载且摘要匹配的材料跳过，进度写入 Journal，重启不重复扣减。
"""
import hashlib
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, "/workspace")
from workbench.evidence import Journal, atomic_json  # noqa: E402

BASE = "http://127.0.0.1:8765"
EVID = Path("/workspace/reconcile/evidence")
JOURNAL = Path("/workspace/reconcile/state/journal.sqlite3")


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> None:
    EVID.mkdir(parents=True, exist_ok=True)
    journal = Journal(JOURNAL)
    collected = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        # 1) 目录分页，收集全部记录编号（不依赖显示顺序推断业务关系）
        page.goto(BASE + "/", wait_until="networkidle")
        total_pages = page.locator('nav[aria-label="资料分页"] a').count()
        record_ids = []
        for n in range(1, total_pages + 1):
            page.goto(f"{BASE}/?page={n}", wait_until="networkidle")
            ids = page.locator("tr[data-record-id]").evaluate_all(
                "rows => rows.map(r => r.getAttribute('data-record-id'))"
            )
            record_ids.extend(ids)
        record_ids = sorted(set(record_ids))
        journal.append("collect:index", {"record_ids": record_ids})

        # 2) 逐条打开详情页，读取字段、摘要，下载附件与文书
        for rid in record_ids:
            page.goto(f"{BASE}/record/{rid}", wait_until="networkidle")
            digest = page.locator('[data-testid="digest"]').inner_text().strip()
            reference = page.locator('[data-testid="reference"]').inner_text().strip()
            fields = json.loads(page.locator('[data-testid="fields"]').inner_text())
            title = page.locator("h1").inner_text().strip()

            att_path = EVID / f"{rid}.json"
            doc_path = None
            # 附件：与摘要完全相同的 JSON 字节
            if not att_path.exists() or sha256(att_path.read_bytes()) != digest:
                with page.expect_download() as dl:
                    page.locator(f'a[href="/attachment/{rid}"]').click()
                dl.value.save_as(att_path)
            att_sha = sha256(att_path.read_bytes())
            assert att_sha == digest, f"{rid} 附件摘要与页面摘要不一致"

            # 文书/扫描副本（与 JSON 摘要是不同对象，单独保存）
            doc_link = page.locator(f'a[href="/document/{rid}"]')
            if doc_link.count():
                with page.expect_download() as dl:
                    doc_link.click()
                suggested = dl.value.suggested_filename or f"{rid}.txt"
                doc_path = EVID / "documents" / suggested
                doc_path.parent.mkdir(parents=True, exist_ok=True)
                if not doc_path.exists():
                    dl.value.save_as(doc_path)
                else:
                    dl.value.save_as(EVID / "documents" / (suggested + ".dup-check"))
                    (EVID / "documents" / (suggested + ".dup-check")).unlink()

            journal.append(
                f"collect:record:{rid}",
                {
                    "id": rid,
                    "title": title,
                    "reference": reference,
                    "attachment": str(att_path),
                    "attachment_sha256": att_sha,
                    "document": str(doc_path) if doc_path else None,
                },
            )
            collected[rid] = {
                "title": title,
                "reference": reference,
                "fields": fields,
                "attachment_sha256": att_sha,
                "document": doc_path.name if doc_path else None,
            }
        browser.close()

    atomic_json(Path("/workspace/reconcile/out/collected.json"), collected)
    print(json.dumps({"records": len(collected), "ids": sorted(collected)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
