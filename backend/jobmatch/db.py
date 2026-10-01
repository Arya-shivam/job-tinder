from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

MIGRATIONS = Path(__file__).with_name("migrations")


def pool_for(url: str) -> AsyncConnectionPool:
    return AsyncConnectionPool(url, min_size=1, max_size=8, kwargs={"row_factory": dict_row}, open=False)


async def migrate(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(634207891334196)")
        await conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY)")
        applied = {row["name"] for row in await (await conn.execute("SELECT name FROM schema_migrations")).fetchall()}
        for migration in sorted(MIGRATIONS.glob("*.sql")):
            if migration.name not in applied:
                await conn.execute(migration.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (migration.name,))
