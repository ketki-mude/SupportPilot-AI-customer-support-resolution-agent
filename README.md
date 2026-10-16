# SupportPilot AI

A customer support resolution agent that resolves routine requests end to end against a sandbox
commerce API, with policy checks, human approval for risky actions, and an audit trail.

Status: phase 1 (foundation). The backend has auth, the data model, and migrations; the mock
commerce API is complete. The agent, RAG, and frontend arrive in later phases.

## Run it locally

Requires Docker with Compose v2.

```sh
cp .env.example .env
# Fill in POSTGRES_PASSWORD, JWT_SECRET (openssl rand -hex 32), and the four SEED_*_PASSWORD values.
make up      # builds and starts db, mock_commerce, and backend; runs migrations
make seed    # creates customer@, agent@, supervisor@, and admin@demo.com
make test    # backend and mock_commerce test suites
make lint    # ruff and mypy
```

| Service       | URL                              |
| ------------- | -------------------------------- |
| Backend API   | http://localhost:8000/api/docs   |
| Mock commerce | http://localhost:8001/docs       |
| Postgres      | localhost:5432                   |

If those ports are taken, change `BACKEND_HOST_PORT`, `MOCK_COMMERCE_HOST_PORT`, or
`DB_HOST_PORT` in `.env`.

## Try it

```sh
curl -s localhost:8000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"email": "supervisor@demo.com", "password": "<SEED_SUPERVISOR_PASSWORD>"}'

# The same Idempotency-Key returns the original refund instead of creating a second one.
for i in 1 2; do
  curl -s localhost:8001/refunds -H 'Idempotency-Key: demo-1' -H 'Content-Type: application/json' \
    -d '{"order_id": "ORD-10002", "amount": "29.98", "reason": "Wrong color"}'; echo
done
```

Set `MOCK_FAILURE_RATE=0.5` in `.env` and run `make up` again to make half of the mock API's
requests fail with a 503 or respond slowly. Mock data lives in memory and resets when the
mock service restarts.
