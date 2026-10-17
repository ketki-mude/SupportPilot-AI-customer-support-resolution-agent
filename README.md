# SupportPilot AI

A customer support resolution agent that resolves routine requests end to end against a sandbox
commerce API, with policy checks, human approval for risky actions, and an audit trail.

Status: phase 4. The agent resolves requests end to end: it classifies the request, finds the
order, checks policy, proposes an action, pauses for a supervisor when the rules require it,
acts, and tells the customer. Customers use a messenger-style chat; staff use a three-pane
console with an approval bar, and an audit log with CSV export.

The agent needs `GOOGLE_API_KEY` in `.env` (or another provider, see `LLM_PROVIDER`).

## Run it locally

Requires Docker with Compose v2.

```sh
cp .env.example .env
# Fill in POSTGRES_PASSWORD, JWT_SECRET (openssl rand -hex 32), and the four SEED_*_PASSWORD values.
make up      # builds and starts db, mock_commerce, and backend; runs migrations
make seed    # creates customer@, agent@, supervisor@, and admin@demo.com
make ingest  # embeds the policy documents in backend/data/policies for search
make test    # backend, mock_commerce, and frontend test suites
make lint    # ruff, mypy, and the frontend type check
make e2e     # browser test of the full approval flow (resets the mock shop's data)
```

Open http://localhost:5173 and sign in:

1. In one browser window, sign in as `customer@demo.com` and write "My headphones arrived
   damaged, I want a refund". Watch the agent's steps; it pauses with "Waiting for approval".
2. In another browser (or a private window), sign in as `supervisor@demo.com`. In the console,
   choose "Needs approval", open the ticket, and approve it.
3. The customer's chat shows the refund confirmation without reloading.

| Service       | URL                              |
| ------------- | -------------------------------- |
| Web app       | http://localhost:5173            |
| Backend API   | http://localhost:8000/api/docs   |
| Mock commerce | http://localhost:8001/docs       |
| Postgres      | localhost:5432                   |

If those ports are taken, change `FRONTEND_HOST_PORT`, `BACKEND_HOST_PORT`,
`MOCK_COMMERCE_HOST_PORT`, or `DB_HOST_PORT` in `.env`.

## Try the agent

From the repo root, with the stack running. Use your `BACKEND_HOST_PORT` if it is not 8000.

```sh
set -a; . ./.env; set +a
API=http://localhost:${BACKEND_HOST_PORT:-8000}/api
token() { curl -s $API/auth/login -H 'Content-Type: application/json' \
  -d "{\"email\": \"$1@demo.com\", \"password\": \"$2\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'; }
CUSTOMER=$(token customer "$SEED_CUSTOMER_PASSWORD")
SUPERVISOR=$(token supervisor "$SEED_SUPERVISOR_PASSWORD")

# 1. The customer reports a problem. No order number needed: the agent matches "headphones".
TICKET=$(curl -s $API/chat/tickets -H "Authorization: Bearer $CUSTOMER" -H 'Content-Type: application/json' \
  -d '{"message": "My headphones arrived damaged, I want a refund"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["ticket"]["id"])')

# 2. Watch the agent work (live events). It pauses for approval because $79.99 is over $50.
curl -sN --max-time 20 $API/chat/tickets/$TICKET/stream -H "Authorization: Bearer $CUSTOMER"

# 3. The supervisor approves (or sends "reject"). Approving twice returns 409.
APPROVAL=$(curl -s "$API/approvals?status=pending" -H "Authorization: Bearer $SUPERVISOR" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["items"][0]["id"])')
curl -s -X POST $API/approvals/$APPROVAL/decision -H "Authorization: Bearer $SUPERVISOR" \
  -H 'Content-Type: application/json' -d '{"decision": "approve"}'

# 4. The refund was issued once and the customer was told.
sleep 2; curl -s $API/tickets/$TICKET -H "Authorization: Bearer $CUSTOMER" | python3 -m json.tool
```

The mock shop keeps its data in memory. Run `docker compose restart mock_commerce` to reset the
demo orders, for example to refund the headphones again.

## Try the mock shop directly

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
