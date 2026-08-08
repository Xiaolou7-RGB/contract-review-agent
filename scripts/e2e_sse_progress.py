"""
E2E verification for Task B (smooth progress bar backend wiring).

Uploads a small sample contract, triggers the async review pipeline,
then consumes the SSE stream and records every event with a timestamp.

Evidence goal: the status sequence parsing -> reviewing -> retrieving
-> revising -> completed must all surface through SSE (before the fix
only parsing -> completed ever appeared).

Writes results to scripts/e2e_sse_evidence.log.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import aiohttp

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8801"

CONTRACT_TEXT = """房屋租赁合同

第一条 租赁标的
甲方将位于广州市天河区某路100号的房屋出租给乙方使用，建筑面积80平方米。

第二条 租赁期限
租赁期限为三年，自2026年1月1日起至2028年12月31日止。

第三条 租金与押金
月租金为人民币5000元，乙方应于每月5日前支付。乙方需支付押金10000元，合同解除后无息退还。

第四条 违约责任
乙方逾期支付租金的，每逾期一日按月租金的5%支付违约金。甲方有权立即解除合同且不退还押金。

第五条 合同解除
任何一方均可随时解除本合同，无需承担任何责任。
"""

EXPECTED = ["parsing", "reviewing", "retrieving", "revising", "completed"]


async def main() -> None:
    events: list[tuple[float, str, str]] = []
    t0 = time.time()

    def stamp() -> float:
        return round(time.time() - t0, 1)

    async with aiohttp.ClientSession() as s:
        # 0. Wait for backend health
        up = False
        for _ in range(90):
            try:
                async with s.get(BASE + "/health", timeout=aiohttp.ClientTimeout(total=3)) as r:
                    if r.status == 200:
                        up = True
                        break
            except Exception:
                pass
            await asyncio.sleep(1)
        if not up:
            print("BACKEND NOT UP after 90s — aborting")
            return
        print(f"[{stamp()}s] backend healthy")

        # 1. Upload sample contract
        form = aiohttp.FormData()
        form.add_field(
            "file", CONTRACT_TEXT.encode("utf-8"),
            filename="e2e_lease.txt", content_type="text/plain",
        )
        async with s.post(BASE + "/api/v1/contract/upload", data=form) as r:
            raw = await r.text()
            if r.status != 200:
                print(f"[{stamp()}s] upload FAILED {r.status}: {raw[:500]}")
                return
            body = json.loads(raw)
            print(f"[{stamp()}s] upload -> {r.status} {body}")
            cid = body["contract_id"]

        # 2. Trigger async review
        async with s.post(BASE + f"/api/v1/contract/run/{cid}") as r:
            raw = await r.text()
            if r.status != 200:
                print(f"[{stamp()}s] run FAILED {r.status}: {raw[:500]}")
                return
            body = json.loads(raw)
            print(f"[{stamp()}s] run -> {r.status} {body}")

        # 3. Consume SSE stream with timestamped event log
        print(f"[{stamp()}s] opening SSE stream...")
        timeout = aiohttp.ClientTimeout(total=900)
        async with s.get(
            BASE + f"/api/v1/contract/run/{cid}/stream?token=e2e", timeout=timeout
        ) as r:
            print(f"[{stamp()}s] SSE http status = {r.status}")
            buffer = ""
            current_event = "message"
            done = False
            async for raw in r.content.iter_any():
                buffer += raw.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                    elif line.startswith("data:"):
                        try:
                            data = json.loads(line[5:].strip())
                        except Exception:
                            continue
                        status = str(data.get("status", ""))
                        events.append((stamp(), current_event, status))
                        print(f"[{stamp()}s] event={current_event} status={status}")
                        if current_event in ("complete", "error"):
                            done = True
                            break
                if done:
                    break

    seq = [st for (_, ev, st) in events if ev in ("progress", "complete")]
    missing = [x for x in EXPECTED if x not in seq]
    print()
    print("=== EVIDENCE ===")
    print("status sequence:", " -> ".join(seq) if seq else "(empty)")
    print("expected all of:", EXPECTED)
    print("missing:", missing if missing else "NONE")

    with open("e2e_sse_evidence.log", "w", encoding="utf-8") as f:
        f.write(f"contract_id={cid}\n")
        for t, ev, st in events:
            f.write(f"{t}s\tevent={ev}\tstatus={st}\n")
        f.write("sequence: " + (" -> ".join(seq) if seq else "(empty)") + "\n")
        f.write("missing: " + (",".join(missing) if missing else "NONE") + "\n")


if __name__ == "__main__":
    asyncio.run(main())
