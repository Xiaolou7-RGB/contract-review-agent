#!/usr/bin/env python3
"""
Create a test user in the database for frontend testing.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

# 读项目根 .env.local（本脚本位于 scripts/ 下）
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.local"))

import asyncpg
from passlib.hash import bcrypt


async def main():
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres123@localhost:15433/contract")
    conn = await asyncpg.connect(db_url)

    try:
        # Create admin user
        admin_hash = bcrypt.hash("admin123")
        await conn.execute(
            """
            INSERT INTO users (username, email, password_hash, role)
            VALUES ('admin', 'admin@contract.local', $1, 'admin')
            ON CONFLICT (username) DO UPDATE SET role = 'admin', email = EXCLUDED.email, password_hash = $1
            """,
            admin_hash,
        )

        # Create regular user
        user_hash = bcrypt.hash("user123")
        await conn.execute(
            """
            INSERT INTO users (username, email, password_hash, role)
            VALUES ('user', 'user@contract.local', $1, 'user')
            ON CONFLICT (username) DO UPDATE SET role = 'user', email = EXCLUDED.email, password_hash = $1
            """,
            user_hash,
        )

        print("Test users created:")
        print("  admin / admin123 (role: admin, email: admin@contract.local)")
        print("  user  / user123  (role: user,  email: user@contract.local)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
