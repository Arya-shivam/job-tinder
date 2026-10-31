import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from jobmatch.db import migrate, pool_for
from jobmatch.ingest import Board, SyncReport, sync_board


@pytest.fixture
async def ingest_db():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for ingestion integration")
    name = f"ingest_{uuid4().hex}"
    async with pool_for(url) as admin:
        async with admin.connection() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            await conn.execute(sql.SQL("CREATE SCHEMA {} ").format(sql.Identifier(name)))
        try:
            async with pool_for(make_conninfo(url, options=f"-c search_path={name},public")) as db:
                await migrate(db)
                yield db
        finally:
            async with admin.connection() as conn:
                await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


class EmbeddingRecorder:
    settings = SimpleNamespace(embedding_model="test-embedding-v1")

    def __init__(self):
        self.inputs = []

    async def embed(self, texts):
        self.inputs.extend(texts)
        return [[1.0] + [0.0] * 1535 for _ in texts]


def greenhouse_posting(id: int, description: str):
    return {"id": id, "title": "Data engineer", "location": {"name": "Remote"},
            "absolute_url": f"https://example.test/jobs/{id}", "content": description}


async def test_changes_embed_only_new_content_and_failures_keep_active(ingest_db):
    board = Board("greenhouse", "publisher", "Example Publishing")
    rows = [greenhouse_posting(1, "&lt;p&gt;Build pipelines &amp;amp; tools.&lt;/p&gt;"),
            greenhouse_posting(2, "&lt;p&gt;Build pipelines &amp;amp; tools.&lt;/p&gt;")]
    failed = False
    model = EmbeddingRecorder()

    def fixture_feed(request):
        assert request.url.params["content"] == "true"
        if failed:
            return httpx.Response(503)
        return httpx.Response(200, json={"jobs": rows, "meta": {"total": len(rows)}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture_feed)) as client:
        assert await sync_board(ingest_db, model, client, board) == SyncReport(2, 1, 0)
        assert "Build pipelines & tools." in model.inputs[0]
        assert await sync_board(ingest_db, model, client, board) == SyncReport(2, 0, 0)
        assert len(model.inputs) == 1
        failed = True
        with pytest.raises(httpx.HTTPStatusError):
            await sync_board(ingest_db, model, client, board)
        async with ingest_db.connection() as conn:
            sources = await (await conn.execute("SELECT job_id FROM job_sources WHERE active")).fetchall()
            assert len(sources) == 2 and sources[0]["job_id"] == sources[1]["job_id"]
        failed = False
        rows[:] = [greenhouse_posting(1, "<p>Build warehouses.</p>"),
                   greenhouse_posting(3, "<p>Build orchestration.</p>")]
        assert await sync_board(ingest_db, model, client, board) == SyncReport(2, 2, 1)
        async with ingest_db.connection() as conn:
            actual = await (await conn.execute(
                """SELECT external_id, active, description FROM job_sources
                   JOIN jobs ON jobs.id = job_sources.job_id ORDER BY external_id"""
            )).fetchall()
        assert [(item["external_id"], item["active"], item["description"]) for item in actual] == [
            ("1", True, "Build warehouses."), ("2", False, "Build pipelines & tools."),
            ("3", True, "Build orchestration.")]
