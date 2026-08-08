# -*- coding: utf-8 -*-
"""Diagnose contract project environment."""
import json
import urllib.request

print("=" * 60)
print("1. Check openai package")
print("=" * 60)
try:
    import openai
    print("openai OK, version:", openai.__version__)
except ImportError as e:
    print("openai MISSING:", e)

print()
print("=" * 60)
print("2. Check Ollama running + models")
print("=" * 60)
try:
    with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as resp:
        data = json.loads(resp.read())
        models = [m["name"] for m in data.get("models", [])]
        print("Ollama running. Models:", models)
except Exception as e:
    print("Ollama ERROR:", e)

print()
print("=" * 60)
print("3. Check Milvus collections")
print("=" * 60)
try:
    from pymilvus import connections, utility
    connections.connect(host="localhost", port="19530", timeout=5)
    cols = utility.list_collections()
    print("Milvus OK. Collections:", cols)
except Exception as e:
    print("Milvus ERROR:", type(e).__name__, str(e)[:200])

print()
print("=" * 60)
print("4. Check ChatOpenAI init + a real LLM call")
print("=" * 60)
try:
    from langchain_community.chat_models import ChatOpenAI
    llm = ChatOpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen2.5:7b",
        temperature=0.1,
        max_tokens=100,
    )
    print("ChatOpenAI init OK, trying a simple call...")
    resp = llm.invoke("Say OK")
    print("LLM call OK:", resp.content[:100])
except Exception as e:
    print("ChatOpenAI ERROR:", type(e).__name__, str(e)[:300])

print()
print("=" * 60)
print("5. Check structured output support")
print("=" * 60)
try:
    from pydantic import BaseModel, Field
    from langchain_community.chat_models import ChatOpenAI

    class TestOut(BaseModel):
        answer: str = Field(default="")

    llm = ChatOpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen2.5:7b",
        temperature=0.1,
        max_tokens=200,
    )
    structured = llm.with_structured_output(TestOut)
    result = structured.invoke("What is 1+1? Put the answer text in 'answer'.")
    print("Structured output OK:", result)
except Exception as e:
    print("Structured ERROR:", type(e).__name__, str(e)[:300])

print()
print("=" * 60)
print("6. Check PostgreSQL contract tables")
print("=" * 60)
try:
    import asyncio
    import asyncpg

    async def check_pg():
        conn = await asyncpg.connect("postgresql://postgres:postgres123@localhost:15432/eduagent")
        try:
            tables = await conn.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'contract%' OR table_name='revision_accepts' OR table_name='idempotent_ops'"
            )
            print("Tables:", [t["table_name"] for t in tables])
            reviews = await conn.fetch("SELECT id, status, contract_type, error_message FROM contract_reviews ORDER BY id")
            for r in reviews:
                print(f"  review id={r['id']} status={r['status']} type={r['contract_type']!r} err={str(r['error_message'])[:60]!r}")
            cnt_clauses = await conn.fetchval("SELECT count(*) FROM contract_clauses")
            cnt_evidence = await conn.fetchval("SELECT count(*) FROM contract_evidence")
            cnt_revisions = await conn.fetchval("SELECT count(*) FROM revision_accepts")
            print(f"  clauses={cnt_clauses}, evidence={cnt_evidence}, revisions={cnt_revisions}")
        finally:
            await conn.close()

    asyncio.run(check_pg())
except Exception as e:
    print("PG ERROR:", type(e).__name__, str(e)[:300])
