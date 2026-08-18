#!/usr/bin/env python3
"""
verify_env.py — 环境自检脚本（统一入口）。

检查项目依赖的核心连通性（全部基于 config 读取，不硬编码）：
  1. 配置加载（.env.local → backend.config）
  2. PostgreSQL 连通
  3. Milvus 连通 + 集合列表
  4. 本地模型文件存在（embedding / reranker）

用法（在项目根目录）：
    .venv\\Scripts\\python scripts/verify_env.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 项目根加入 sys.path，使 backend 包可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import get_settings


def check_config() -> bool:
    print("=" * 60)
    print("1. 配置加载（.env.local）")
    print("=" * 60)
    try:
        s = get_settings()
        print(f"  database_url : {s.database_url}")
        print(f"  milvus       : {s.milvus_host}:{s.milvus_port}")
        print(f"  llm          : {s.llm_api_base} / {s.llm_model}")
        print(f"  llm_api_key  : {'已配置' if s.llm_api_key else '缺失'}")
        return True
    except Exception as e:
        print(f"  配置加载失败: {type(e).__name__} {e}")
        return False


async def check_postgres() -> bool:
    print("=" * 60)
    print("2. PostgreSQL 连通")
    print("=" * 60)
    try:
        import asyncpg
        conn = await asyncpg.connect(get_settings().database_url, timeout=5)
        try:
            n = await conn.fetchval("SELECT count(*) FROM users")
            print(f"  PostgreSQL 连通 ✓ users 表 {n} 条记录")
            return True
        finally:
            await conn.close()
    except Exception as e:
        print(f"  PostgreSQL 失败: {type(e).__name__} {e}")
        return False


def check_milvus() -> bool:
    print("=" * 60)
    print("3. Milvus 连通")
    print("=" * 60)
    try:
        from pymilvus import connections, utility
        s = get_settings()
        connections.connect(host=s.milvus_host, port=s.milvus_port, timeout=5)
        cols = utility.list_collections()
        print(f"  Milvus 连通 ✓ 集合: {cols}")
        return True
    except Exception as e:
        print(f"  Milvus 失败: {type(e).__name__} {e}")
        return False


def check_models() -> bool:
    print("=" * 60)
    print("4. 本地模型文件")
    print("=" * 60)
    s = get_settings()
    ok = True
    for name, path in [
        ("BGE-M3 (embedding)", s.bge_m3_model_path),
        ("BGE-Reranker", s.bge_reranker_model_path),
    ]:
        exists = Path(path).exists()
        print(f"  {name}: {'存在' if exists else '缺失'} ({path})")
        ok = ok and exists
    return ok


async def main():
    results = [
        ("配置", check_config()),
        ("PostgreSQL", await check_postgres()),
        ("Milvus", check_milvus()),
        ("模型文件", check_models()),
    ]

    print()
    print("=" * 60)
    print("自检结果")
    print("=" * 60)
    for name, ok in results:
        print(f"  {'✓' if ok else '✗'} {name}")
    all_ok = all(ok for _, ok in results)
    print(f"\n{'全部通过 ✓' if all_ok else '存在失败项 ✗'}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
