"""
check_llm.py — verify the LLM stack end to end.
1) Raw OpenAI client -> DeepSeek chat completion
2) langchain ChatOpenAI + with_structured_output (the exact path the pipeline uses)
Run: D:\contract\.venv\Scripts\python.exe check_llm.py
"""
import os
import sys

# Load .env.local manually (no dependency on app imports)
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.local")
with open(ENV_FILE, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.stdout.reconfigure(encoding="utf-8")

base = os.environ["LLM_API_BASE"]
key = os.environ["LLM_API_KEY"]
model = os.environ["LLM_MODEL"]
print(f"[config] base={base} model={model} key=***{key[-4:]}")

# --- 1) raw openai client ---
from openai import OpenAI

client = OpenAI(base_url=base, api_key=key)
r = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "回复两个字：正常"}],
    max_tokens=20,
)
print("[1] raw openai OK ->", r.choices[0].message.content)

# --- 2) langchain structured output (target state of backend/core/llm.py) ---
from importlib.metadata import version as _v

print(f"[stack] langchain={_v('langchain')} community={_v('langchain-community')} langgraph={_v('langgraph')} langchain-openai={_v('langchain-openai')}")

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field


class RiskCard(BaseModel):
    level: str = Field(description="风险等级：高/中/低/无")
    reason: str = Field(description="一句话理由")


llm = ChatOpenAI(
    base_url=base,
    api_key=key,
    model=model,
    temperature=0,
)
structured = llm.with_structured_output(RiskCard, method="function_calling")
card = structured.invoke("合同条款：乙方违约需赔偿甲方全部损失，包括间接损失。判断风险等级。")
print("[2] langchain structured output OK ->", card)
print("ALL_CHECKS_PASSED")
