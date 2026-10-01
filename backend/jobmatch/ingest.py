import argparse
import asyncio
import hashlib
import html
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from psycopg_pool import AsyncConnectionPool

from .config import Settings
from .db import migrate, pool_for
from .models import Models

BATCH_SIZE = 32
PAGE_SIZE = 100
SLUG = re.compile(r"[a-zA-Z0-9_-]+")
CURRENCY = re.compile(r"[A-Z]{3}")


@dataclass(frozen=True, slots=True)
class Board:
    provider: str
    slug: str
    company: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.slug}"


@dataclass(frozen=True, slots=True)
class Posting:
    external_id: str
    url: str
    company: str
    title: str
    description: str
    locations: tuple[str, ...]
    workplace: str | None
    employment_type: str | None
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None

    def content(self) -> tuple:
        return (self.company, self.title, self.description, list(self.locations), self.workplace,
                self.employment_type, self.salary_min, self.salary_max, self.salary_currency)

    def digest(self) -> bytes:
        return hashlib.sha256(json.dumps(self.content(), ensure_ascii=False).encode()).digest()

    def embedding_input(self) -> str:
        return f"{self.title}\n{self.company}\n{'; '.join(self.locations)}\n{self.description[:6000]}"


@dataclass(frozen=True, slots=True)
class SyncReport:
    postings: int
    embedded: int
    closed: int


class _PlainText(HTMLParser):
    blocks = frozenset({"article", "blockquote", "br", "div", "h1", "h2", "h3", "h4", "hr",
                        "li", "ol", "p", "section", "table", "td", "tr", "ul"})

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hidden: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden = tag
        elif self.hidden is None and tag in self.blocks:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag == self.hidden:
            self.hidden = None
        elif self.hidden is None and tag in self.blocks:
            self.parts.append(" ")

    def handle_data(self, data):
        if self.hidden is None:
            self.parts.append(data)


def clean_text(text: str) -> str:
    return " ".join(text.split())


def html_text(value: str) -> str:
    parser = _PlainText()
    parser.feed(html.unescape(value) if re.search(r"&lt;/?(?:p|div|ul|h[1-4]|strong|br)\b", value, re.I) else value)
    parser.close()
    return clean_text("".join(parser.parts))


def configured_boards(path: Path) -> dict[str, Board]:
    with path.open("rb") as stream:
        config = tomllib.load(stream)
    boards = {}
    for provider, companies in config.items():
        if provider not in {"greenhouse", "lever"} or not isinstance(companies, dict):
            raise ValueError(f"Unknown job board family: {provider}")
        for slug, company in companies.items():
            if not SLUG.fullmatch(slug) or not isinstance(company, str) or not company.strip():
                raise ValueError(f"Invalid board entry: {provider}:{slug}")
            board = Board(provider, slug, company.strip())
            boards[board.key] = board
    if not boards:
        raise ValueError("Configure at least one public job board.")
    return boards


def _locations(values: list[str]) -> tuple[str, ...]:
    cleaned = {clean_text(value) for value in values if isinstance(value, str) and value.strip()}
    return tuple(sorted(cleaned, key=lambda value: (value.casefold(), value)))


def _workplace_metadata(value: object) -> str | None:
    if isinstance(value, str):
        normalized = re.sub(r"[\s-]+", "", value.casefold())
        return normalized if normalized in {"remote", "hybrid", "onsite"} else None
    return None


def _employment(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return {"fulltime": "full-time", "parttime": "part-time", "contract": "contract",
            "contractor": "contract", "intern": "internship", "internship": "internship",
            "temporary": "temporary"}.get(re.sub(r"[\s-]+", "", value.casefold()))


def _salary(currency: object, low: object, high: object) -> tuple[int | None, int | None, str | None]:
    def amount(value: object) -> int | None:
        return int(value) if type(value) in {int, float} and 0 < value <= 2_147_483_647 else None

    low, high = amount(low), amount(high)
    if (not isinstance(currency, str) or not CURRENCY.fullmatch(currency)
            or (low is None and high is None) or (low is not None and high is not None and high < low)):
        return None, None, None
    return low, high, currency


def _greenhouse_salary(row: dict) -> tuple[int | None, int | None, str | None]:
    # Multiple ranges may be regional; a single universal floor cannot be inferred.
    ranges = row.get("pay_input_ranges") or []
    if len(ranges) == 1:
        entry = ranges[0]
        low, high = entry.get("min_cents"), entry.get("max_cents")
        if entry.get("interval") in {"year", "yearly", "annual", "annually"}:
            return _salary(entry.get("currency_type"), low / 100 if type(low) is int else None,
                           high / 100 if type(high) is int else None)
    return None, None, None


def _greenhouse(board: Board, row: dict) -> Posting:
    metadata = {str(item.get("name", "")).casefold(): item.get("value")
                for item in (row.get("metadata") or [])}
    location = (row.get("location") or {}).get("name")
    workplace = next((known for name in ("workplace type", "workplace", "work model", "remote type")
                      if (known := _workplace_metadata(metadata.get(name)))), None)
    if workplace is None and isinstance(location, str):
        signals = {x for value in re.split(r"[,;|()]|\s+-\s+", location)
                   if (x := _workplace_metadata(value))}
        workplace = signals.pop() if len(signals) == 1 else None
    return _posting(row["id"], row["absolute_url"], board.company, row["title"],
                    html_text(row["content"] or ""), _locations([location]), workplace,
                    _employment(metadata.get("employment type") or metadata.get("commitment")),
                    _greenhouse_salary(row))


def _lever(board: Board, row: dict) -> Posting:
    category = row.get("categories") or {}
    body = row.get("descriptionPlain") or html_text(row.get("description") or "")
    lists = [clean_text(f"{item.get('text', '')} {html_text(item.get('content') or '')}")
             for item in (row.get("lists") or [])]
    additional = row.get("additionalPlain") or html_text(row.get("additional") or "")
    salary = row.get("salaryRange") or {}
    annual = (_salary(salary.get("currency"), salary.get("min"), salary.get("max"))
              if salary.get("interval") in {"year", "yearly", "annual", "annually"} else (None, None, None))
    return _posting(row["id"], row["hostedUrl"], board.company, row["text"],
                    clean_text(" ".join([body, *lists, additional])),
                    _locations([category.get("location"), *(category.get("allLocations") or [])]),
                    _workplace_metadata(row.get("workplaceType")), _employment(category.get("commitment")), annual)


def _posting(external_id, url, company, title, description, locations, workplace, employment, salary) -> Posting:
    link = urlsplit(url)
    if (type(external_id) not in {int, str} or not str(external_id) or not clean_text(title)
            or link.scheme not in {"http", "https"} or not link.hostname or link.username is not None):
        raise ValueError("A board returned an invalid posting identity, title, or URL.")
    return Posting(str(external_id), url, company, clean_text(title), description, locations, workplace, employment, *salary)


async def fetch_board(client: httpx.AsyncClient, board: Board) -> list[Posting]:
    if board.provider == "greenhouse":
        response = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{board.slug}/jobs",
                                    params={"content": "true"})
        response.raise_for_status()
        payload = response.json()
        rows = payload["jobs"]
        if not isinstance(rows, list) or payload.get("meta", {}).get("total", len(rows)) != len(rows):
            raise ValueError("Greenhouse returned an incomplete board snapshot.")
        return [_greenhouse(board, row) for row in rows if row.get("internal_job_id", True) is not None]
    postings = []
    seen = set()
    offset = 0
    while True:
        response = await client.get(f"https://api.lever.co/v0/postings/{board.slug}",
                                    params={"mode": "json", "skip": offset, "limit": PAGE_SIZE})
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, list):
            raise ValueError("Lever returned an invalid board snapshot.")
        for row in page:
            posting = _lever(board, row)
            if posting.external_id in seen:
                raise ValueError("Lever postings shifted during pagination; retry the board snapshot.")
            seen.add(posting.external_id)
            postings.append(posting)
        if len(page) < PAGE_SIZE:
            return postings
        offset += len(page)


def board_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "JobMatch/0.1 (public job feeds)"})


def vector_literal(values: list[float]) -> str:
    return json.dumps(values, separators=(",", ":"), allow_nan=False)


async def _store_batch(conn, models: Models, board: Board, content: dict[bytes, Posting], sources: list[tuple]) -> int:
    hashes = list(content)
    model = models.settings.embedding_model
    async with conn.transaction():
        versions = await (await conn.execute(
            "SELECT id, content_hash, embedding_model FROM jobs WHERE content_hash = ANY(%s)", (hashes,),
        )).fetchall()
    job_ids = {row["content_hash"]: row["id"] for row in versions}
    current = {row["content_hash"] for row in versions if row["embedding_model"] == model}
    pending = [key for key in hashes if key not in current]
    vectors = await models.embed([content[key].embedding_input() for key in pending]) if pending else []
    async with conn.transaction(), conn.cursor() as cursor:
        if pending:
            await cursor.executemany(
                """INSERT INTO jobs (company, title, description, locations, workplace, employment_type,
                                     salary_min, salary_max, salary_currency, content_hash, embedding, embedding_model)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
                   ON CONFLICT (content_hash) DO UPDATE
                   SET embedding = EXCLUDED.embedding, embedding_model = EXCLUDED.embedding_model
                   RETURNING id""",
                [(*content[key].content(), key, vector_literal(vector), model)
                 for key, vector in zip(pending, vectors, strict=True)], returning=True,
            )
            for key in pending:
                job_ids[key] = (await cursor.fetchone())["id"]
                cursor.nextset()
        await cursor.executemany(
            """INSERT INTO job_sources (board, external_id, job_id, url)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (board, external_id) DO UPDATE
               SET job_id = EXCLUDED.job_id, url = EXCLUDED.url, active = true
               WHERE (job_sources.job_id, job_sources.url, job_sources.active)
                     IS DISTINCT FROM (EXCLUDED.job_id, EXCLUDED.url, true)""",
            [(board.key, external_id, job_ids[key], url) for external_id, url, key in sources],
        )
    return len(pending)


async def sync_board(db: AsyncConnectionPool, models: Models, client: httpx.AsyncClient, board: Board) -> SyncReport:
    async with db.connection() as conn:
        # Serialize manual/scheduled syncs of the same board, including their network snapshot.
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (board.key,))
        try:
            postings = await fetch_board(client, board)
            seen = set()
            for posting in postings:
                if posting.external_id in seen:
                    raise ValueError("The board returned a repeated posting ID.")
                seen.add(posting.external_id)
            embedded = 0
            for start in range(0, len(postings), BATCH_SIZE):
                content, sources = {}, []
                for posting in postings[start:start + BATCH_SIZE]:
                    key = posting.digest()
                    content[key] = posting
                    sources.append((posting.external_id, posting.url, key))
                embedded += await _store_batch(conn, models, board, content, sources)
            async with conn.transaction():
                result = await conn.execute(
                    "UPDATE job_sources SET active = false WHERE board = %s AND active AND NOT (external_id = ANY(%s))",
                    (board.key, list(seen)),
                )
            return SyncReport(len(postings), embedded, result.rowcount)
        finally:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (board.key,))


async def _main(board_key: str) -> None:
    settings = Settings()
    boards = configured_boards(settings.board_file)
    if board_key not in boards:
        raise SystemExit(f"Unknown board '{board_key}'. Use provider:slug from boards.toml.")
    models = Models(settings)
    try:
        async with pool_for(settings.database_url) as db, board_client() as client:
            await migrate(db)
            with models.operation("board_ingest"):
                result = await sync_board(db, models, client, boards[board_key])
            print(f"{board_key}: postings={result.postings} embeds={result.embedded} closed={result.closed}")
    finally:
        await models.close()


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description="Synchronize one configured board immediately.")
    cli.add_argument("board", help="provider:slug, e.g. greenhouse:figma")
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(_main(cli.parse_args().board), loop_factory=loop_factory)
