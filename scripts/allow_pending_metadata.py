"""One-time migration: allow unknown company / fiscal fields without fake values."""
import asyncio
from transcript_to_db import create_pool


async def main():
    pool = await create_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SHOW CREATE TABLE earnings_calls")
                _, definition = await cur.fetchone()
                changes = []
                for name in ("company_name", "fiscal_year", "fiscal_quarter"):
                    # Preserve the database's actual type, collation, defaults and comments.
                    line = next(line.strip().rstrip(",") for line in definition.splitlines()
                                if line.strip().startswith(f"`{name}` "))
                    if "NOT NULL" in line:
                        line = line.replace("NOT NULL", "NULL", 1)
                        changes.append("MODIFY COLUMN " + line)
                if changes:
                    await cur.execute("ALTER TABLE earnings_calls " + ", ".join(changes))
                    print("earnings_calls 已允許公司名稱、財報年度／季度暫時為 NULL。")
                else:
                    print("欄位已允許 NULL，不需修改。")
    finally:
        pool.close()
        await pool.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
