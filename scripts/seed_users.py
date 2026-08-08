#!/usr/bin/env python3
"""
Create a test user in the database for frontend testing.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncpg
from passlib.hash import bcrypt


async def main():
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres123@localhost:15432/eduagent")
    conn = await asyncpg.connect(db_url)

    try:
        # Create admin user
        admin_hash = bcrypt.hash("admin123")
        await conn.execute(
            """
            INSERT INTO users (username, password_hash, role)
            VALUES ('admin', $1, 'admin')
            ON CONFLICT (username) DO UPDATE SET role = 'admin', password_hash = $1
            """,
            admin_hash,
        )

        # Create regular user
        user_hash = bcrypt.hash("user123")
        await conn.execute(
            """
            INSERT INTO users (username, password_hash, role)
            VALUES ('user', $1, 'user')
            ON CONFLICT (username) DO UPDATE SET role = 'user', password_hash = $1
            """,
            user_hash,
        )

        print("Test users created:")
        print("  admin / admin123 (role: admin)")
        print("  user  / user123  (role: user)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
