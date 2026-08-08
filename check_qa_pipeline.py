"""End-to-end QA module check — real PG + Milvus + DeepSeek, zero mocks.

Covers: session create -> ask -> SSE stream -> grounded citations -> DB persist,
plus scope guard (off-topic question), 403 (other user's contract), 409 (re-stream),
demo mode (no token at all — the anonymous frontend flow), and Milvus round-lookup
of every cited source_id (anti-fabrication).

Results are written to check_qa_result.txt (UTF-8) to avoid console mojibake.
"""
import os
import sys
import json
import asyncio

sys.path.insert(0, "D:/contract")
from dotenv import load_dotenv
load_dotenv("D:/contract/.env.local")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import asyncpg

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres123@localhost:15432/eduagent"
)
CONTRACT_ID = 9999  # data produced by check_pipeline.py (3 cards / 33 evidence / 3 revisions)

OUT = open("D:/contract/check_qa_result.txt", "w", encoding="utf-8")


def log(msg: str):
    print(msg)
    OUT.write(msg + "\n")
    OUT.flush()


# ── SSE frame parser (tolerates \r\n\r\n and \n\n, mirrors frontend) ──────

def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for frame in text.replace("\r\n", "\n").split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        event, data_raw = "message", ""
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_raw += line[5:].strip()
        if data_raw:
            try:
                events.append((event, json.loads(data_raw)))
            except json.JSONDecodeError:
                pass
    return events


async def main():
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.dependencies import create_token

    db = await asyncpg.connect(DATABASE_URL)
    try:
        # ── 0. Clean stale QA data for the check contract (history lesson) ──
        await db.execute(
            "DELETE FROM contract_qa_messages WHERE session_id IN "
            "(SELECT id FROM contract_qa_sessions WHERE contract_id = $1)",
            CONTRACT_ID,
        )
        await db.execute(
            "DELETE FROM contract_qa_sessions WHERE contract_id = $1", CONTRACT_ID
        )
        await db.execute(
            "INSERT INTO contract_reviews (id, user_id, filename, original_filename, contract_type, status) "
            "VALUES (9999, 1, 'check_pipeline.txt', 'check_pipeline.txt', '采购合同', 'completed') "
            "ON CONFLICT (id) DO UPDATE SET status = 'completed'"
        )
        user = await db.fetchrow("SELECT id, username, role FROM users WHERE id = 1")
        assert user, "users.id=1 missing"
    finally:
        await db.close()
    log("[QA-0] cleaned stale QA data; contract 9999 ready")

    token = create_token(user["id"], user["username"], user["role"])
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        # ── 1. Create session ──
        r = client.post(
            "/api/v1/contract/qa/session",
            json={"contract_id": CONTRACT_ID}, headers=headers,
        )
        assert r.status_code == 200, f"session create failed: {r.status_code} {r.text}"
        session_id = r.json()["session_id"]
        log(f"[QA-1] session created: id={session_id}")

        answers: dict[str, dict] = {}

        def ask_and_stream(name: str, question: str) -> dict:
            r1 = client.post(
                f"/api/v1/contract/qa/session/{session_id}/ask",
                json={"question": question}, headers=headers,
            )
            assert r1.status_code == 200, f"ask failed: {r1.status_code} {r1.text}"
            message_id = r1.json()["message_id"]

            url = f"/api/v1/contract/qa/message/{message_id}/stream?token={token}"
            chunks_text = ""
            with client.stream("GET", url) as resp:
                assert resp.status_code == 200, f"stream failed: {resp.status_code}"
                for piece in resp.iter_text():
                    chunks_text += piece
            events = parse_sse(chunks_text)
            deltas = "".join(d.get("text", "") for ev, d in events if ev == "delta")
            cites = next((d.get("items", []) for ev, d in events if ev == "citations"), [])
            kinds = [ev for ev, _ in events]
            assert kinds[0] == "citations", f"citations must come first, got {kinds[:3]}"
            assert kinds[-1] in ("done", "error"), f"stream did not finish: {kinds[-3:]}"
            log(f"[{name}] message_id={message_id} events={len(events)} answer_len={len(deltas)} citations={len(cites)} finished={kinds[-1]}")
            OUT.write(f"[{name}] answer: {deltas}\n")
            OUT.write(f"[{name}] citations: {json.dumps(cites, ensure_ascii=False)}\n\n")
            OUT.flush()
            return {"message_id": message_id, "text": deltas, "citations": cites, "finished": kinds[-1]}

        # ── 2. Q1: risk question (context = review cards + evidence) ──
        answers["Q1"] = ask_and_stream("QA-2/Q1", "这份合同有哪些主要风险？")
        assert answers["Q1"]["finished"] == "done"
        assert len(answers["Q1"]["text"]) > 20, "Q1 answer too short"

        # ── 3. Q2: legal-basis question (must produce grounded citations) ──
        answers["Q2"] = ask_and_stream("QA-3/Q2", "违约责任条款的法律依据是什么？")
        assert answers["Q2"]["finished"] == "done"
        q2_cites = answers["Q2"]["citations"]
        assert len(q2_cites) > 0, "Q2 produced no citations — grounding broken"
        log(f"[QA-3] Q2 citations: {[c['article_no'] for c in q2_cites]}")

        # ── 4. Anti-fabrication: every cited source_id must exist in Milvus ──
        from pymilvus import MilvusClient
        mc = MilvusClient(uri=f"http://{os.getenv('MILVUS_HOST', 'localhost')}:{os.getenv('MILVUS_PORT', '19530')}")
        for c in q2_cites:
            sid = c["source_id"]
            rows = mc.query(
                collection_name="civil_code_hybrid",
                filter=f'id == "{sid}"',
                output_fields=["id", "article_no"],
            )
            assert rows, f"citation source_id not found in Milvus: {sid}"
            log(f"[QA-4] citation {c['ref']} source_id={sid} -> Milvus OK (article_no={rows[0].get('article_no')})")

        # ── 5. Q3: scope guard (off-topic question must not cite statutes) ──
        import re
        answers["Q3"] = ask_and_stream("QA-5/Q3", "帮我写一首关于春天的诗")
        assert answers["Q3"]["finished"] == "done"
        assert not re.search(r"\[\d+\]", answers["Q3"]["text"]), \
            "scope-guard failure: off-topic answer cites statute numbers"
        log("[QA-5] scope guard OK: off-topic answer contains no [n] citations")

        # ── 6. 409: re-streaming a completed message is rejected ──
        r6 = client.get(
            f"/api/v1/contract/qa/message/{answers['Q1']['message_id']}/stream?token={token}"
        )
        assert r6.status_code == 409, f"expected 409, got {r6.status_code}"
        log("[QA-6] re-stream rejected with 409 OK")

        # ── 7. 403: another user cannot open a session on this contract ──
        db = await asyncpg.connect(DATABASE_URL)
        try:
            row = await db.fetchrow(
                "INSERT INTO users (username, password_hash, role) "
                "VALUES ('qa_check_user2', 'x', 'user') RETURNING id"
            )
            user2_id = row["id"]
        finally:
            await db.close()
        try:
            token2 = create_token(user2_id, "qa_check_user2", "user")
            r7 = client.post(
                "/api/v1/contract/qa/session",
                json={"contract_id": CONTRACT_ID},
                headers={"Authorization": f"Bearer {token2}"},
            )
            assert r7.status_code == 403, f"expected 403, got {r7.status_code}"
            log("[QA-7] cross-user session blocked with 403 OK")
        finally:
            db = await asyncpg.connect(DATABASE_URL)
            try:
                await db.execute("DELETE FROM users WHERE id = $1", user2_id)
            finally:
                await db.close()

        # ── 8. Messages persisted with status=completed + citations ──
        r8 = client.get(f"/api/v1/contract/qa/session/{session_id}/messages", headers=headers)
        assert r8.status_code == 200
        msgs = r8.json()["messages"]
        user_msgs = [m for m in msgs if m["role"] == "user"]
        asst_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert len(user_msgs) == 3 and len(asst_msgs) == 3, f"got {len(user_msgs)} user / {len(asst_msgs)} assistant"
        assert all(m["status"] == "completed" for m in asst_msgs), "some assistant messages not completed"
        q2_db = asst_msgs[1]
        assert q2_db["citations"], "Q2 citations not persisted"
        assert q2_db["citations"][0]["source_id"] == q2_cites[0]["source_id"]
        log(f"[QA-8] DB persist OK: {len(msgs)} messages, all completed, citations match")

        # ── 9. Demo mode: no token at all (anonymous frontend flow) ──
        r9 = client.post(
            "/api/v1/contract/qa/session", json={"contract_id": CONTRACT_ID}
        )  # intentionally no Authorization header
        assert r9.status_code == 200, f"demo-mode session create failed: {r9.status_code} {r9.text}"
        demo_session_id = r9.json()["session_id"]
        r9b = client.get(f"/api/v1/contract/qa/session/{demo_session_id}/messages")
        assert r9b.status_code == 200, f"demo-mode messages failed: {r9b.status_code} {r9b.text}"
        log(f"[QA-9] demo mode (no token) OK: session {demo_session_id} created and readable")

    # Clean up the demo-mode probe session
    db = await asyncpg.connect(DATABASE_URL)
    try:
        await db.execute(
            "DELETE FROM contract_qa_messages WHERE session_id = $1", demo_session_id
        )
        await db.execute(
            "DELETE FROM contract_qa_sessions WHERE id = $1", demo_session_id
        )
    finally:
        await db.close()

    OUT.close()
    print("ALL_CHECKS_PASSED")


if __name__ == "__main__":
    asyncio.run(main())
