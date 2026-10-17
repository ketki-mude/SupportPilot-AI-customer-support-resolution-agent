# Deployment notes

SupportPilot is built to run locally with Docker Compose. This page lists what changes for a
real deployment. It is a checklist, not a tested production setup.

## Images

| Service | Build | Notes |
| --- | --- | --- |
| backend | `docker build backend` | Without `INSTALL_DEV=true`, so no test tools. Runs `alembic upgrade head`, then uvicorn. The embedding model is baked into the image. |
| frontend | `docker build --target prod frontend` | nginx serves the built app and proxies `/api` to `backend:8000`, with security headers and a CSP. |
| db | `pgvector/pgvector:pg16` | Or a managed Postgres 16 with the `vector` extension. |

Do not deploy `mock_commerce` or `mock_commerce_eval`. Point `MOCK_COMMERCE_URL` at a real
order system that offers the same operations (customer, orders, refunds, replacements,
cancellations, with idempotency keys), or keep the mock only for a demo environment.

## Settings to change

| Variable | Production value |
| --- | --- |
| `APP_ENV` | `production` (refresh cookies become `Secure`) |
| `JWT_SECRET` | 32+ random bytes, from a secret store |
| `POSTGRES_PASSWORD`, `DATABASE_URL` | From a secret store |
| `GOOGLE_API_KEY` (or another provider's key) | A billing-enabled key; the free tier allows only a few requests per day |
| `CORS_ORIGINS` | Leave empty when the frontend and API share an origin (the nginx setup) |
| `LOG_JSON` | `true`, shipped to your log store; every line carries a request id |
| `LANGSMITH_TRACING` | Optional; traces are scrubbed of emails, phone numbers, and card numbers |
| `SEED_*_PASSWORD` | Do not run `make seed` in production; create real staff accounts instead |

Terminate TLS in front of nginx (a load balancer or ingress).

## Run one backend replica

Rate limits, live agent events (SSE), and the evaluation job live in the backend process's
memory. With two replicas, a customer's stream could connect to a replica that is not running
their agent, and each replica would count rate limits separately. Scaling out needs:

- a shared store (for example Redis) for rate limits and for publishing agent events,
- a job queue for agent runs and evaluations instead of in-process background tasks.

Paused approvals already survive restarts and could resume on any replica: their state is in
the Postgres checkpointer.

## Before go-live

- Replace the sandbox with real systems and rerun the evaluation against a staging copy of them.
- Review the business thresholds (`AUTO_APPROVE_MAX_USD`, `SUPERVISOR_APPROVAL_MAX_USD`,
  `REFUND_WINDOW_DAYS`, `MIN_CONFIDENCE`) with the support and finance teams.
- Back up Postgres. The audit log is append-only and is the record of what the agent did.
