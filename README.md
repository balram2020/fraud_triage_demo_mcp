# Demo 5: Full Local Enterprise Runtime

FastAPI + PostgreSQL + Redis running locally via Docker Compose.

**Key idea:** Docker Compose gives you a local version of an enterprise runtime.

## Overview

> **FastAPI makes the agent callable. Postgres and Redis make it operable.**

| Layer | Role |
|-------|------|
| FastAPI | HTTP API, routes — makes the agent **callable** |
| PostgreSQL | Sessions, messages, traces, costs — **remembers** durable state |
| Redis | Rate limits, budgets, locks, idempotency — **controls** runtime behavior |
| Docker Compose | Local multi-service runtime |

## Setup

```bash
cd d36_full_local_enterprise_runtime
cp .env.example .env   # set OPENAI_API_KEY (or copy ../.env from the repo root)
docker compose up --build
```

The API uses OpenAI by default (`DEFAULT_MODEL`, default `gpt-4.1-nano`). For offline runs without a key, set `USE_MOCK_LLM=true`.

Note: Docker Compose V2 uses `docker compose` (space). Legacy installs may use `docker-compose`.

Wait until all services are healthy (~30 seconds on first build). Then confirm:

```bash
curl http://localhost:8000/health
```

## Test Endpoints

### Health

```bash
curl http://localhost:8000/health
```

Expected when healthy:

```json
{
  "status": "ok",
  "postgres": "ok",
  "redis": "ok",
  "hint": null
}
```

If a dependency is down, `status` becomes `degraded` and `hint` explains what to fix:

```json
{
  "status": "degraded",
  "postgres": "error",
  "redis": "ok",
  "hint": "Postgres: Postgres is not ready yet. Wait ~30 seconds after `docker compose up`..."
}
```

### Chat

```bash
RESPONSE=$(curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant_demo",
    "user_id": "user_123",
    "message": "My order has not arrived and I was charged twice."
  }')

echo "$RESPONSE" | python3 -m json.tool
SESSION_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
TRACE_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['trace_id'])")
```

Example output:

```json
{
  "trace_id": "trace_a1b2c3d4e5f6",
  "request_id": "req_123456789abc",
  "session_id": "sess_987654321fed",
  "answer": "Order #48291 is delayed by 2 business days. Duplicate charge of $49.99 detected...",
  "skills_used": ["orders_skill", "billing_skill", "policy_skill"],
  "escalated": true,
  "total_cost_usd": 0.006,
  "status": "completed"
}
```

### Session Messages

```bash
curl "http://localhost:8000/v1/sessions/${SESSION_ID}/messages"
```

Example output (2 rows — user + assistant):

```json
[
  {
    "id": "msg_abc123",
    "session_id": "sess_987654321fed",
    "role": "user",
    "content": "My order has not arrived and I was charged twice.",
    "request_id": "req_123456789abc",
    "created_at": "2026-05-22T13:00:00.000000"
  },
  {
    "id": "msg_def456",
    "session_id": "sess_987654321fed",
    "role": "assistant",
    "content": "Order #48291 is delayed by 2 business days...",
    "request_id": "req_123456789abc",
    "created_at": "2026-05-22T13:00:00.100000"
  }
]
```

### Trace Events

```bash
curl "http://localhost:8000/v1/traces/${TRACE_ID}"
```

Example output (4 graph steps):

```json
[
  {"event_type": "node_start", "node_name": "input_guardrail", "...": "..."},
  {"event_type": "node_start", "node_name": "skill_planner", "...": "..."},
  {"event_type": "skill_call", "node_name": "skill_executor", "...": "..."},
  {"event_type": "node_start", "node_name": "synthesis", "...": "..."}
]
```

### Idempotency (retries should not create duplicate work)

```bash
curl -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant_demo",
    "user_id": "user_123",
    "message": "My order has not arrived and I was charged twice.",
    "idempotency_key": "demo-idempotency-001"
  }'

# Repeat — returns the same cached response
curl -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant_demo",
    "user_id": "user_123",
    "message": "Different message — still a duplicate key",
    "idempotency_key": "demo-idempotency-001"
  }'
```

## MCP Server Integration

This demo includes a Model Context Protocol (MCP) server that exposes the internal business skills (like `get_order_status`, `lookup_transaction`, etc.) so they can be consumed by external LLMs, agent frameworks, or Claude Desktop.

### Connecting via SSE (Server-Sent Events)

When running the project via `docker compose up --build`, an `mcp-server` service is automatically started. It listens on port `8001`.

You can connect your remote MCP clients to this URL:
`http://localhost:8001/sse`

### Connecting via STDIO (Local/Claude Desktop)

If you want to use the server locally with Claude Desktop, ensure you have installed the project dependencies locally:
```bash
pip install -r requirements.txt
```

Then configure your `mcp.json` or Claude configuration file to point to the server:
```json
{
  "mcpServers": {
    "FraudTriageSimulatedTools": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/absolute/path/to/fraud_triage_demo_mcp"
    }
  }
}
```

## How to Inspect Postgres Records

Postgres is the **memory** layer — it stores what happened so you can audit it later.

1. Open a shell inside the postgres container:

```bash
docker compose exec postgres psql -U agent_user -d agent_service
```

2. Run simple SELECT queries:

```sql
-- Who chatted and when?
SELECT id, tenant_id, user_id, status FROM sessions ORDER BY created_at DESC LIMIT 5;

-- What was said?
SELECT role, left(content, 60) AS preview FROM messages ORDER BY created_at DESC LIMIT 5;

-- How did the agent decide?
SELECT trace_id, event_type, node_name FROM trace_events ORDER BY created_at DESC LIMIT 8;

-- What did it cost?
SELECT tenant_id, model_name, cost_usd FROM cost_usage ORDER BY created_at DESC LIMIT 5;
```

3. Exit psql with `\q`.

**One-liners from your terminal (no interactive psql):**

```bash
docker compose exec postgres psql -U agent_user -d agent_service -c "SELECT id, role, left(content, 50) FROM messages;"
docker compose exec postgres psql -U agent_user -d agent_service -c "SELECT trace_id, event_type, node_name FROM trace_events;"
docker compose exec postgres psql -U agent_user -d agent_service -c "SELECT tenant_id, cost_usd FROM cost_usage;"
```

Postgres stores durable history. If the business may ask about it later, persist it.

## How to Inspect Redis Keys

Redis is the **control** layer — it holds short-lived counters and locks, not the full conversation history.

1. List keys created by the API:

```bash
docker compose exec redis redis-cli KEYS '*'
```

2. Inspect specific control keys:

```bash
# Tenant daily spend counter
docker compose exec redis redis-cli GET 'budget:tenant:tenant_demo:daily'

# Per-user rate limit counter
docker compose exec redis redis-cli GET 'rate:tenant_demo:user_123'

# Cached idempotency response
docker compose exec redis redis-cli GET 'idempotency:demo-idempotency-001'
```

3. Check TTL (time-to-live) on a key:

```bash
docker compose exec redis redis-cli TTL 'rate:tenant_demo:user_123'
```

Redis protects the system in real time — rate limits, budgets, locks, and idempotency.

| Key pattern | Purpose |
|-------------|---------|
| `rate:{tenant}:{user}` | User-level rate limit counter |
| `budget:tenant:{tenant}:daily` | Tenant daily spend counter |
| `budget:session:{session}` | Session spend counter |
| `lock:session:{session}` | Session lock (sequential chat) |
| `idempotency:{key}` | Cached response for retries |

## Postgres vs Redis

| Store | Role | Example keys / tables |
|-------|------|----------------------|
| **PostgreSQL** | Durable audit trail | `sessions`, `messages`, `trace_events`, `cost_usage` |
| **Redis** | Runtime guardrails | `rate:...`, `budget:...`, `lock:...`, `idempotency:...` |

On every `/v1/chat` request the API:
1. Checks Redis idempotency, rate limit, budget, and session lock
2. Persists session + user message to Postgres
3. Runs the LangGraph skills pipeline (`app/graph/`)
4. Persists trace events, cost usage, and assistant message to Postgres
5. Updates Redis budget counter and releases the session lock in a `finally` block

## Troubleshooting

### Docker Desktop not running

**Symptom:** `Cannot connect to the Docker daemon` or `docker: command not found`

**Fix:**
1. Open Docker Desktop and wait until it shows "Running"
2. Verify: `docker info`
3. Re-run: `docker compose up --build`

### Port 8000 already in use

**Symptom:** `Bind for 0.0.0.0:8000 failed: port is already allocated`

**Fix:**
1. Find what is using the port: `lsof -i :8000` (macOS/Linux)
2. Stop the other process, or change the API port in `docker-compose.yml`:
   ```yaml
   ports:
     - "8001:8000"
   ```
3. Then use `curl http://localhost:8001/health`

### Port 5432 already in use

**Symptom:** Postgres container fails to start; local Postgres may already be running

**Fix:**
1. Stop local Postgres, or change the mapping in `docker-compose.yml`:
   ```yaml
   ports:
     - "5433:5432"
   ```
2. If connecting from outside Docker, update `DATABASE_URL` to use port `5433`

### Port 6379 already in use

**Symptom:** Redis container fails to start

**Fix:**
1. Stop local Redis, or change the mapping:
   ```yaml
   ports:
     - "6380:6379"
   ```
2. If connecting from outside Docker, update `REDIS_URL` to use port `6380`

### Database not ready yet

**Symptom:** `/health` returns `"postgres": "error"` or API logs mention Postgres not ready

**Fix:**
1. Wait ~30 seconds after `docker compose up --build`
2. Check postgres logs: `docker compose logs postgres`
3. Look for: `database system is ready to accept connections`
4. Check service status: `docker compose ps` (postgres should be `healthy`)

### Redis not ready yet

**Symptom:** `/health` returns `"redis": "error"`

**Fix:**
1. Check redis logs: `docker compose logs redis`
2. Look for: `Ready to accept connections`
3. Retry: `curl http://localhost:8000/health`

### Redis host not found (api cannot resolve `redis`)

**Symptom:** Startup log shows `redis_unavailable` with hint *"Inside Docker Compose use host `redis`"* even though `REDIS_URL=redis://redis:6379/0`

**Cause:** The Redis container is running but **not attached to the Compose network** (often after a partial restart or stale container).

**Fix:**
```bash
docker compose down
docker compose up --build
```

Or recreate just Redis:

```bash
docker compose up -d --force-recreate redis
docker compose restart api
```

Verify DNS from the API container:

```bash
docker compose exec api getent hosts redis
```

You should see an IP address. If `getent` returns nothing, Redis is still off-network.

### API returns 429 (rate limit)

**Symptom:** `Rate limit exceeded` during repeated curls

**Fix:** Wait 60 seconds, or restart redis: `docker compose restart redis`

### API returns 409 (session busy)

**Symptom:** `Session busy — lock already held`

**Fix:** Wait ~30 seconds for lock TTL to expire, or restart redis

## Architecture

```
Client → FastAPI (api)
           ├── Redis controls (rate limit, budget, lock, idempotency)
           ├── LangGraph (input_guardrail → skill_planner → skill_executor → synthesis)
           └── PostgreSQL (sessions, messages, trace_events, cost_usage, escalations)
```

## Extension Points (not implemented yet)

- Alembic migrations
- pgAdmin / Adminer
- Enterprise RAG
- Celery async workers
- Streaming responses
- Cloud deployment
