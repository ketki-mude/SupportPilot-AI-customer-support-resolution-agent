Project spec and working rules for SupportPilot AI. Treat them as binding.

You are a senior full-stack and AI engineer. We are building SupportPilot AI, a portfolio-grade customer support resolution agent. It is not a plain chatbot: it resolves complaints end to end by calling real (sandbox) APIs, checking policy with RAG, pausing for human approval on risky actions, and recording an audit trail.

## Working rules
- Build in phases. Complete only the phase I ask for, then stop and summarize what you built and how to verify it.
- Do not invent features, endpoints, or files outside this spec. If something is ambiguous, state your assumption in one sentence and continue.
- Write typed, tested, production-style code. Python: type hints, Pydantic v2, async where it helps. TypeScript: strict mode.
- After each phase run the linters and tests and fix failures before finishing.
- Never hardcode secrets. All config comes from environment variables via a single settings class.
- Keep functions small. Add short docstrings. No dead code, no TODO placeholders in finished phases.

## Problem statement
Support teams spend most of their time on repetitive requests such as refunds, missing orders, and cancellations. To resolve one, a human agent must open several systems, verify the customer, look up the order, read the policy, decide, take the action, and update the ticket. Typical chatbots only answer questions from FAQs, so the customer still waits for a human to actually fix the problem. Fully autonomous bots are risky because a wrong refund costs real money and there is no accountability.

## Goal
Build an AI agent that resolves routine support requests end to end, safely: it takes real actions through APIs, follows company policy exactly, asks a human before any risky action, and records everything it does.

## Objectives
1. Resolve common requests (refund, replacement, order status, cancellation) from first message to verified outcome with no manual steps when the case is low risk.
2. Ground every decision in company policy retrieved through RAG, with a citation.
3. Keep humans in control: pause for supervisor approval above the approval threshold, and escalate unclear or high-risk cases.
4. Make unsafe actions impossible: business rules are enforced in code, and the agent can never act without a recorded decision.
5. Be reliable: retries, idempotent actions, and clean escalation when systems fail.
6. Be auditable: every agent step and human decision is logged and reviewable.
7. Be measurable: evaluate on a test set and report correct resolution rate, policy compliance, unauthorized actions (target 0), latency, and cost.

## Non-goals
Real payments, real customer data, voice or email channels, and model training. Everything runs against a sandbox.

## The business scenario
E-commerce company "Acme". Example: a customer writes "My headphones arrived damaged, I want a refund."
The agent must: create a ticket, understand the request, look up the order through an API, check the refund policy using RAG, propose an action, pause for supervisor approval if risky, call the sandbox refund API, update the ticket, and tell the customer.

## Tech stack
Backend: Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2.0 (async) + Alembic, PostgreSQL 16 with pgvector, LangGraph (with the Postgres checkpointer), LangChain (tools and retriever helpers), tenacity (retries), structlog, pytest + pytest-asyncio + httpx, ruff, mypy.
LLM: provider-swappable wrapper. Default `LLM_PROVIDER=gemini` using `langchain-google-genai` (`ChatGoogleGenerativeAI`), API key from env `GOOGLE_API_KEY`, model name from env `LLM_MODEL` (default `gemini-2.5-flash`; I will change it in `.env` if a newer model is better). Also support Anthropic and OpenAI via `LLM_PROVIDER`. All LLM outputs that drive logic use `with_structured_output` validated by Pydantic, with one automatic retry on invalid output.
Embeddings: `fastembed` with `BAAI/bge-small-en-v1.5` (384 dims) by default, swappable.
Auth: JWT (access token), bcrypt password hashing, role-based access control.
Streaming: Server-Sent Events for live agent progress.
Observability: LangSmith tracing (optional via env), structlog JSON logs, per-run token, cost, and latency records stored in the database.
Frontend: React 18, Vite, TypeScript, Tailwind CSS, shadcn/ui, React Router, TanStack Query, lucide-react icons.
DevOps: Docker, Docker Compose, GitHub Actions, Makefile.

## Services (docker compose)
- `db`: image `pgvector/pgvector:pg16`
- `backend`: FastAPI app on port 8000
- `mock_commerce`: separate FastAPI app on port 8001 that simulates Acme's order system
- `mock_commerce_eval`: a second instance for evaluations only, with admin endpoints that load each case's data and faults (no host port)
- `frontend`: Vite dev server (nginx in production) on port 5173

## Repository structure
```
supportpilot/
├── README.md
├── docker-compose.yml
├── .env.example
├── Makefile
├── .github/workflows/ci.yml, .github/scripts/ci-env.sh
├── docs/
│   ├── architecture.md          (mermaid diagrams)
│   ├── deployment.md
│   ├── eval-report.md           (written by `make eval`)
│   └── screenshots/
├── backend/
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── alembic.ini
│   ├── alembic/versions/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/        config.py, security.py, logging.py, errors.py
│   │   ├── api/
│   │   │   ├── deps.py
│   │   │   └── routes/  auth.py, chat.py, tickets.py, approvals.py, audit.py, evals.py, health.py
│   │   ├── db/
│   │   │   ├── session.py
│   │   │   └── models/  user.py, ticket.py, message.py, approval.py, audit_log.py, agent_run.py, policy_chunk.py
│   │   ├── schemas/
│   │   ├── agents/
│   │   │   ├── graph.py, state.py, guardrails.py, llm.py
│   │   │   ├── nodes/   intake.py, customer_data.py, policy_rag.py, resolution.py,
│   │   │   │            approval_gate.py, executor.py, notifier.py, escalation.py
│   │   │   ├── tools/   orders.py, refunds.py, tickets.py, notifications.py
│   │   │   └── prompts/ *.md
│   │   ├── rag/         chunking.py, embeddings.py, ingest.py, retriever.py
│   │   ├── services/    ticket_service.py, approval_service.py, audit_service.py, policy_engine.py
│   │   ├── evals/       dataset.py, harness.py, metrics.py, mock_llm.py, quota.py, report.py, jobs.py, cli.py
│   │   └── observability/ tracing.py, cost.py
│   ├── data/policies/   refund_policy.md, shipping_policy.md, billing_policy.md, cancellation_policy.md
│   ├── scripts/         seed.py, ingest_policies.py
│   └── tests/           unit/, integration/, e2e/
├── mock_commerce/
│   ├── Dockerfile
│   ├── main.py
│   └── seed_data.json
├── evals/
│   ├── dataset/cases.jsonl, build_cases.py
│   ├── run_evals.py             (entry point; the code is in backend/app/evals so it is typed and tested)
│   └── reports/
└── frontend/
    ├── package.json, vite.config.ts, tailwind.config.ts, Dockerfile
    └── src/
        ├── main.tsx, App.tsx, routes.tsx
        ├── lib/         api.ts, auth.tsx, sse.ts, format.ts
        ├── hooks/       useAgentStream.ts, useTickets.ts, useApprovals.ts
        ├── components/  ui/ (shadcn), ChatWindow.tsx, MessageBubble.tsx, AgentTrace.tsx,
        │                TicketList.tsx, AiSummary.tsx, ApprovalBar.tsx, CustomerPanel.tsx,
        │                AuditTable.tsx, StatusBadge.tsx
        └── pages/       Login.tsx, Chat.tsx, Console.tsx, AuditLog.tsx, Evals.tsx
```

## Roles and demo users (created by the seed script)
- `customer@demo.com` (customer), owns demo orders
- `agent@demo.com` (agent): can view tickets and the audit log
- `supervisor@demo.com` (supervisor): everything an agent can do, plus approve or reject
- `admin@demo.com` (admin): everything, plus run evaluations
Passwords come from env vars in the seed script. Never hardcode them in source.

## Mock commerce API (port 8001)
- `GET /customers/{id}`
- `GET /customers/{id}/orders`
- `GET /orders/{order_id}` (items, price, status, payment_status, delivered_at, estimated_delivery_at, payment_method_last4, remedy)
- `POST /refunds` (requires `Idempotency-Key` header; same key returns the original result; body: order_id, amount, reason)
- `POST /replacements` (same idempotency rule)
- `POST /orders/{order_id}/cancel` (same idempotency rule; only when status is `processing`, otherwise 409). Cancels the order and, in the same call, refunds a captured payment or voids an authorized one.
- `GET /health`
Order statuses: `processing`, `shipped`, `in_transit`, `delivered`, `cancelled`. Payment status: `authorized`, `captured`, `voided`.
One remedy per order: an order records at most one remedy (`refund` or `replacement`); a second remedy request returns 409. A replay with the same Idempotency-Key still returns the original result.
It must support a configurable failure rate via env `MOCK_FAILURE_RATE` (random 503s and slow responses) so retry and error handling can be demonstrated and tested. Seed 30 realistic orders across 10 customers, including edge cases: delivered 45 days ago, already refunded, already replaced, wrong owner, high value, in transit, in transit past its estimated delivery date, shipped, several processing (captured and authorized), and already cancelled.

## Database models
- `users`: id, email, password_hash, role, customer_id (nullable), created_at
- `tickets`: id, public_id (TCK-####), customer_id, order_id, category (refund, replacement, missing_order, cancellation, billing, other), status (open, awaiting_approval, resolved, escalated, rejected), priority, summary, created_at, updated_at
- `messages`: id, ticket_id, sender (customer, agent, supervisor, system), content, created_at
- `approvals`: id, ticket_id, proposed_action (jsonb), status (pending, approved, rejected), decided_by, note, decided_at, created_at
- `audit_logs`: id, ticket_id, actor (user id or "agent"), action, tool_name, input (jsonb), output (jsonb), status, created_at. Append-only: no update or delete routes or ORM methods.
- `agent_runs`: id, ticket_id, thread_id, input_tokens, output_tokens, cost_usd, latency_ms, status, created_at
- `policy_chunks`: id, source, section, content, embedding (vector 384), created_at

## Business rules (deterministic code in `services/policy_engine.py`, never decided by the LLM)
- Every action: the customer must own the order, otherwise deny and write an audit entry. Escalate on a suspected fraud flag, unknown intent, or LLM confidence below 0.6.
- Money amounts come from the order data, never from the LLM. The LLM picks which items a request is about; the engine computes the amount and denies any proposal whose amount does not match.
- Refunds and replacements follow the same rules (a replacement is valued at the price of the replaced items):
  - Only delivered orders, within 30 days of delivery (day 30 allowed, day 31 not, counted in UTC calendar days).
  - Not delivered: share status only. Exception: an order still in transit after its estimated delivery date escalates to a human (likely lost).
  - One remedy per order. If a refund or replacement already exists, deny.
  - Up to $50: auto-approve. Over $50 up to $500: supervisor approval. Over $500: escalate to a human.
- Cancellations:
  - Status `processing`: auto-approve at any amount. Call the cancel endpoint, which refunds a captured payment in the same call.
  - Status `shipped` or `in_transit`: escalate to a human.
  - Status `delivered`: cannot cancel. Explain and offer the refund path.
  - Status `cancelled`: share the status, take no action.
  - Config flag `CANCEL_USE_REFUND_LIMITS` (default false). When true, unshipped cancellations use the same $50 and $500 limits as refunds.
- All thresholds and the window live in config, not in prompts.

## Agent workflow (LangGraph)
State (Pydantic): ticket_id, customer_id, messages, intent, order, policy_snippets, proposal, decision, approval, result, errors.
Nodes and edges:
1. `intake`: classify intent (refund, replacement, missing_order, cancellation, billing, other) with structured output, create or load the ticket, extract the order id. If the customer gives no order number, the `list_my_orders` tool (customer id from the authenticated token, never from the model; audited) lists their orders and the model proposes which one matches; code keeps only ids from that list. Several matches: ask, describing them by item and date. None: ask for more detail. At most 5 clarification turns, then escalate.
2. `customer_data`: tools get_customer and get_order through the mock API; verify ownership.
3. `policy_rag`: retrieve the top 4 policy chunks with section citations, searching only the documents that govern the classified intent (refund and replacement: refund policy; cancellation, missing_order, billing: their own policies). The similarity floor applies only to searches without a known intent. Phase 6 finding: with a global floor, plain customer wording scored below it, the model saw no policy, and escalated requests the rules allow.
4. `resolution`: LLM returns a structured `Proposal` (action: refund, replacement, cancel, info, escalate; items; amount; reason; confidence; citations).
5. `policy_gate`: call `policy_engine.evaluate(proposal, order)`; returns decision auto_approve, needs_approval, deny, or escalate. This is plain Python. The engine itself writes nothing, so this node must write an audit entry for every decision (rule, reason, amount), including ownership denials, so an attempt on another customer's order is always recorded.
6. `approval_gate`: only when needs_approval. Create an approval row, set ticket status to awaiting_approval, then call LangGraph `interrupt()`. The run resumes with `Command(resume=...)` when a supervisor decides.
7. `executor`: only runs actions that are auto-approved or have an approved approval row. Calls the refund, replacement, or cancel tool with idempotency key `refund-{ticket_id}`, `replacement-{ticket_id}`, or `cancel-{ticket_id}`. Retries transient errors with tenacity (3 attempts, exponential backoff).
8. `notifier`: update the ticket, write the customer message, write the audit log.
9. `escalation`: set status escalated, tell the customer a human will follow up, notify supervisors.
Follow-up customer messages: open ticket, continue the conversation; awaiting approval, a fixed templated reply with no model call and no graph run (the message is saved for the supervisor); resolved, rejected, or escalated, save the message, do not reopen, send one templated reply, and set the ticket's `customer_replied` flag (exposed in the ticket API). On an escalated ticket the reply says the specialist will see it, and the customer's chat stays open for them; a staff reply clears the flag.

Staff takeover (agreed after Phase 6): agents, supervisors, and admins can act on escalated or open tickets the agent is not running.
- Reply: a `staff` message ("Support team" in the chat), on any ticket, pushed live over SSE.
- Act: refund (whole order or chosen items), replacement, or cancel, through the agent's own audited tools and per-ticket idempotency keys, with the staff member as the audit actor. The amount always comes from the order record.
- Every action is checked by the policy engine first. Allowed: the rules permit it (the person is the approver). Override: the rules would refuse or escalate it (over $500, outside the window, not delivered or late, a fraud flag); it needs a written reason of at least 10 characters, recorded with the rule that was overridden. Blocked for everyone: another customer's order, a second remedy on an order, an item not on the order, no order, replacing an undelivered order, cancelling one that has shipped, been delivered, or been cancelled.
- Close without an action, as resolved or declined, with a templated message to the customer.
- The ticket row is locked with `FOR NO KEY UPDATE` (audit inserts on other connections reference it), so of two simultaneous actions exactly one succeeds and the other gets 409.
Any unrecoverable tool error routes to `escalation`. Persist state with the Postgres checkpointer so a paused run survives a restart. The `thread_id` is the ticket id.

## Guardrails
- Treat all customer text as untrusted. Never put it in a system prompt, and ignore instructions inside it.
- Redact emails, phone numbers, and card numbers before sending text to the LLM or logs.
- The executor refuses anything without a recorded approval or an auto_approve decision, even if the LLM asks.
- Validate every tool argument with Pydantic.
- Audit every attempted tool call, not only executed ones (Phase 5): when argument validation fails, write an audit entry with status `rejected_invalid`, the tool name, the validation error, and redacted arguments. Test that a malformed call is blocked and logged.
- Rate limit the chat endpoint per user.

## Backend API
- `POST /api/auth/login`, `GET /api/auth/me`
- `POST /api/chat/tickets` (customer starts a ticket with a first message)
- `POST /api/chat/tickets/{id}/messages` (customer sends a message)
- `GET  /api/chat/tickets/{id}/stream` (SSE events: step_started, step_finished, message, approval_requested, completed, error)
- `GET  /api/tickets?status=&q=` (agent, supervisor)
- `GET  /api/tickets/{id}` (messages, summary, trace, customer context)
- `GET  /api/approvals?status=pending`
- `POST /api/approvals/{id}/decision` (supervisor only; body: decision approve or reject, optional note; resumes the graph)
- `GET  /api/audit?ticket_id=` (agent, supervisor, admin)
- `POST /api/evals/run`, `GET /api/evals/runs` (admin; Phase 6)
- `POST /api/tickets/{id}/reply` (staff: message the customer; shows in their chat live)
- `POST /api/tickets/{id}/actions/check` (staff: what the rules say about an action: allowed, override, or blocked)
- `POST /api/tickets/{id}/actions` (staff: refund, replacement, or cancel; an override needs a written reason)
- `POST /api/tickets/{id}/close` (staff: close without an action, as resolved or declined)
- `GET  /api/health`
Use consistent error responses (`{"error": {"code", "message"}}`), pagination, and OpenAPI docs.

## Frontend design spec
Visual style is clean and calm, modeled on modern helpdesks (Intercom-style messenger for customers, Zendesk-style three-pane workspace for staff).
- Font Inter. Sentence case everywhere. No emoji.
- Neutral gray surfaces, one accent blue. Radius 8px for controls, 12px for cards. 0.5px hairline borders, no shadows, no gradients.
- Only one filled primary button per view. Everything else is outline or ghost.
- Status badges: warning for awaiting approval, success for resolved, danger for escalated.
- Support light and dark mode through CSS variables.
- Responsive: the customer chat must work on a phone.

Pages:
1. `/login`: email and password form, redirect by role (customer goes to /chat, staff go to /console).
2. `/chat` (customer): messenger-style chat. Header with the Acme mark and "Replies in seconds". Customer bubbles on the right in the accent color, agent bubbles on the left in gray. A small status badge under the latest agent message (for example "Waiting for approval"). Live agent step updates over SSE. Input bar with send button.
3. `/console` (agent, supervisor): three panes.
   - Top bar: logo, search box with Cmd+K quick jump.
   - Left: ticket list with "Needs approval" and "With a specialist" filters, title, customer name, and a small priority dot.
   - Middle: an AI summary card at the top (2 to 3 lines), the conversation, then an approval bar at the bottom: refund amount, "Policy match, agent 92% sure", Reject (outline) and Approve (the single filled button). Supervisors can add an optional note.
   - Right: customer context (name, tier, order card, history).
   - Approve and Reject must call the API, update the UI optimistically, and show an inline result message.
   - On tickets the agent handed over, a "Handle this ticket" panel replaces the approval bar: reply, choose an action (with items), see the rules' verdict, write the reason an override needs, or close without an action. The action button is the single filled button. Closed tickets keep a reply box only.
4. `/audit`: table with time, actor, action, result badge. Filter by ticket. Export as CSV.
5. `/evals` (admin, Phase 6): four metric cards, a bar list of accuracy by category, and a "Run evaluation" button.
Handle loading, empty, and error states on every page.

## Evaluation plan
`evals/dataset/cases.jsonl` holds 80 cases. Each has: id, customer_message, fixture (order and customer data), expected_intent, expected_action, expected_requires_approval, must_not (for example "issue refund"). Include cases for: valid refunds under and over $50, outside the window, wrong owner, already refunded, in transit, prompt injection attempts, vague messages, and tool failures.
Metrics: correct resolution rate, policy compliance rate, unauthorized-action rate (target exactly 0), tool-call success rate, average latency, average cost per ticket. Write results to `evals/reports/` and `docs/eval-report.md`. Never fabricate numbers; the README must only quote results from real runs.
Phase 6 rules (agreed):
- Isolation: every run recreates its own `<db>_eval` database (refusing any other name), and every case resets the dedicated `mock_commerce_eval` shop to its own fixture and faults. Evaluations never touch the demo tickets or the demo shop.
- Reproducibility: temperature 0 (`LLM_TEMPERATURE`); every report records model, provider, the temperature used, UTC date, git commit (with `-dirty` for uncommitted changes), and the dataset's sha256. Exception: models that reject any temperature but their own (OpenAI reasoning models such as gpt-6-luna) run with `LLM_TEMPERATURE=default`, and the report says so.
- Cost and quota: `--limit N`; rate-limit errors are retried with backoff; a daily-quota error stops the run, leaves the affected case unscored, and marks the report incomplete. The estimated cost is printed before a live run and the actual cost after it.
- CI never calls a model: the 10-case smoke run uses an offline keyword stand-in and must pass all ten with zero unauthorized actions. The full live run is the manual `make eval` (needs `GOOGLE_API_KEY`).
- Honest reporting: reports list every failed case with reasons; percentages are truncated, never rounded up; effects are read back from the shop, not from the agent. The README's results block is generated from a complete, full-dataset live report and is never edited by hand.

## Testing notes
- Backend tests may run the mock shop in-process (mounted read-only by `docker-compose.test.yml`, used only by `make test`) to force failures deterministically. The running backend and any production image never include mock shop code.
- End-to-end tests (Phase 4 Playwright, Phase 6 eval smoke run) call the running mock service over HTTP, so the real network path is covered.

## Definition of done for the whole project
- `docker compose up` starts everything and the seeded demo works end to end.
- Tests pass in CI; ruff, mypy, and the frontend type check pass.
- README has: overview, architecture diagram (mermaid), screenshots, setup steps, demo script, eval results, and design decisions.
