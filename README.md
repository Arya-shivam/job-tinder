# Job Match Agent

A candidate-side job search agent. Upload a text-based PDF resume, set preferences in chat, and review up to three roles per day. Each recommendation includes evidence quoted from the posting and the gaps to check. Hard requirements are applied before ranking, and excluded roles have recorded reasons. The app suggests resume changes but applies only approved edits to an export; it never submits an application.

## Run locally

Requirements: Docker with Compose, and an OpenAI API key for model-backed features.

1. Copy `.env.example` to `.env` and set `OPENAI_API_KEY`.
2. Run `docker compose up --build -d`.
3. Import a board and repeat to verify incremental embeddings:

   ```sh
   docker compose exec api python -m jobmatch.ingest greenhouse:figma
   docker compose exec api python -m jobmatch.ingest greenhouse:figma
   ```

   The second run should print `embeds=0` if the posting content and embedding model have not changed.
4. Open [http://localhost:38080](http://localhost:38080), create an account, upload a resume, and select **Run now**. The API, database, queue and worker are not published to the host by Compose.

Change `WEB_PORT` in `.env` to use a different local port. Leave `WEB_ORIGIN` unset unless you need a different host or scheme.

The scheduler enqueues ingest for the 50 configured public boards at 03:00 UTC and digests at 06:00 UTC. An on-demand digest uses the latest already-ingested postings. Initial population makes paid embedding calls; to constrain that bill, start with a small list in `backend/jobmatch/boards.toml`. A failed board request does not deactivate postings from the last successful snapshot. Job data updates by content hash rather than source timestamp. Changing the embedding model requires resyncing configured boards; retrieval does not compare vectors from different models.

Try preferences such as “I'm looking for product data scientist roles in London,” then “Only include remote roles and exclude Example Corp.” Hard rules cover required locations, workplace and employment types, blocked companies, and comparable annual salary floors. Unknown facts do not satisfy a hard requirement. Salary is a screen on reported ranges, not a prediction of an offer. When no posting survives or the reranker cannot verify a recommendation, the digest can be empty.

## Architecture

- **API:** Python 3.12, FastAPI, Pydantic v2, psycopg. The OpenAI SDK handles embeddings and strict structured outputs; FastAPI owns the chat tool loop.
- **Store/search:** Postgres with pgvector and English full-text search. Reciprocal rank fusion combines lexical and vector results after hard filtering. The reranker cannot add arbitrary jobs or ship a reason whose quote is not in the normalized posting.
- **Jobs:** Redis and ARQ. Postgres persists digests, exclusion rules and candidate state; the queue does not carry resume contents or chat text.
- **Web:** Next.js, React and Tailwind. A same-origin API proxy carries session cookies and PDF uploads.
- **Observability:** Set both Langfuse keys to record model versions, token counts and timings. `chat_request` is the full chat span for p95 latency; `daily_digest` groups the provider calls for one scheduled digest to measure cost per active candidate per day. The app does not send candidate identifiers, prompts, outputs or attachments to Langfuse.

## Privacy and fairness

Stored resumes are private and scoped to the signed-in account. The app removes locally detected personal identifiers and protected cues before sending resume or chat text to the provider; the ranker sees a purpose-built profile and preference projection, not PDF bytes, chat history or account identity. Local redaction is not a guarantee against every possible free-text identifier, and counterfactual ranking checks measure invariance under the tested changes, not population-level fairness. **Delete my data** removes the account, sessions, resume, approved edits and digests from the active database. Hosted backup retention and model-provider retention are governed by their own policies; configure those policies before accepting real resumes.

## Run the quality gate

With a pgvector database and Redis available on localhost (use isolated test services):

```sh
cd backend
export TEST_DATABASE_URL=postgresql://jobmatch:jobmatch@127.0.0.1:55432/jobmatch
export TEST_REDIS_URL=redis://127.0.0.1:56379/0
uv run --locked pytest -q -s -m 'not live'
RUN_LIVE_OPENAI=1 OPENAI_API_KEY=… uv run --locked pytest -q -s -m live
```

`.github/workflows/quality.yml` runs the deterministic checks and production web build on pushes and PRs. Set the repository's `OPENAI_API_KEY` secret to enable the metered live-model checks on trusted pushes and the nightly schedule. The deterministic gate does not measure real model quality or cost.

### Measured baseline

Local Postgres/pgvector run: 30 labelled synthetic profiles, 90 identity/institution swaps and 120 rerank attempts. This run exercises the application SQL and output validators with deterministic model test doubles.

| Metric | Result |
| --- | ---: |
| Dealbreaker violations | 0 |
| Ungrounded reasons shipped | 0 |
| Valid structured outputs | 120/120 |
| Recall@50 | 100% |
| nDCG@3, retrieval and rerank | 98.8% |
| Counterfactual top-three order agreement | 90/90 |
| Privacy marker leaks | 0 |

The checked-in baseline allows at most a five-percentage-point drop in retrieval/rerank metrics. Live-model output quality, cost per user per day and p95 chat latency have not been measured. A local ingest smoke against Khan Academy’s live feed processed three postings and requested zero further test embeddings on the unchanged second sync.

## Deploy on Railway

1. Connect the GitHub repository to three services in one [Railway project](https://docs.railway.com/deployments/monorepo): `web` rooted at `/web`; `api` and `worker` rooted at `/backend`. They build from the checked-in Dockerfiles. Override only `worker`'s start command with `arq jobmatch.worker.WorkerSettings`. Keep one always-on worker initially.
2. Add [Postgres with pgvector](https://railway.com/deploy/pgvector) and Redis. Use persistent volumes, set a backup/retention policy, and keep both services private. Use a direct Postgres connection (not transaction-mode PgBouncer) for the board synchronization advisory lock.
3. Set `DATABASE_URL`, `REDIS_URL` and `OPENAI_API_KEY` on `api` and `worker`. They apply migrations at startup. Keep `api` private, set `PORT=8000`, and use `/healthz` as the health check.
4. Expose only `web` on port 3000. Set `PORT=3000` and `API_BASE_URL=http://api.railway.internal:8000` on it. Set `WEB_ORIGIN=https://<public-web-host>` and `COOKIE_SECURE=true` on the API. Use the exact public origin, including any nonstandard port, without a path.
5. Run the initial single-board import from the API service using `python -m jobmatch.ingest greenhouse:figma`. Verify registration, resume upload, matching and deletion on the public HTTPS domain before inviting users.
