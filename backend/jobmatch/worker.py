import logging
from datetime import UTC, date, datetime
from uuid import UUID

import httpx
from arq import Retry, cron
from arq.connections import RedisSettings
from psycopg import OperationalError

from .config import Settings
from .db import migrate, pool_for
from .ingest import board_client, configured_boards, sync_board
from .models import ModelResponseError, Models
from .matching import DigestAccountMissing, DigestProfileChanged, DigestProfileMissing, generate_digest

log = logging.getLogger(__name__)
settings = Settings()


async def startup(ctx):
    ctx["boards"] = configured_boards(settings.board_file)
    ctx["db"] = db = pool_for(settings.database_url)
    await db.open(wait=True)
    try:
        await migrate(db)
    except BaseException:
        await db.close()
        raise
    ctx["client"] = board_client()
    ctx["models"] = Models(settings)


async def shutdown(ctx):
    await ctx["client"].aclose()
    await ctx["models"].close()
    await ctx["db"].close()

async def ingest_one(ctx, board_key: str):
    models = ctx["models"]
    try:
        with models.operation("board_ingest"):
            report = await sync_board(ctx["db"], models, ctx["client"], ctx["boards"][board_key])
    except (httpx.TransportError, OperationalError, ModelResponseError) as exc:
        raise Retry(defer=60 * ctx["job_try"]) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429 or exc.response.status_code >= 500:
            raise Retry(defer=60 * ctx["job_try"]) from exc
        raise
    log.info("%s postings=%d embeds=%d closed=%d", board_key, report.postings, report.embedded, report.closed)


async def digest_one(ctx, account_id: str, day: str):
    models = ctx["models"]
    try:
        with models.operation("daily_digest"):
            await generate_digest(ctx["db"], models, UUID(account_id), date.fromisoformat(day))
    except (DigestAccountMissing, DigestProfileChanged, DigestProfileMissing):
        return
    except (OperationalError, ModelResponseError) as exc:
        raise Retry(defer=60 * ctx["job_try"]) from exc


async def enqueue_ingest(ctx):
    day = datetime.now(UTC).date()
    async with ctx["db"].connection() as conn:
        await conn.execute("UPDATE job_sources SET active = false WHERE active AND NOT (board = ANY(%s))",
                           (list(ctx["boards"]),))
    for board_key in ctx["boards"]:
        await ctx["redis"].enqueue_job("ingest_one", board_key, _job_id=f"board:{day}:{board_key}")


async def enqueue_digests(ctx):
    day = datetime.now(UTC).date()
    async with ctx["db"].connection() as conn:
        accounts = await (await conn.execute(
            """SELECT id FROM accounts a WHERE profile IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM digests d WHERE d.account_id = a.id AND d.day = %s AND d.revision = a.revision
            )""", (day,),
        )).fetchall()
    for account in accounts:
        account_id = str(account["id"])
        await ctx["redis"].enqueue_job("digest_one", account_id, day.isoformat(), _job_id=f"digest:{day}:{account_id}")


class WorkerSettings:
    functions = [ingest_one, digest_one]
    cron_jobs = [cron(enqueue_ingest, hour=3, minute=0), cron(enqueue_digests, hour=6, minute=0)]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    timezone = UTC
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 4
    job_timeout = 900
    keep_result = 86_400
    log_results = False
