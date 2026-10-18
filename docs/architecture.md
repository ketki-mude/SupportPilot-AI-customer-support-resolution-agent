# Architecture

SupportPilot resolves customer support requests end to end. A LangGraph agent reads the request,
looks up the customer's order through a commerce API, retrieves the governing policy, and
proposes one action. Deterministic Python decides whether that action runs, waits for a
supervisor, or goes to a human. Every step is written to an append-only audit log.

## Services

```mermaid
flowchart LR
    customer([Customer]) --> web
    staff([Agent / supervisor / admin]) --> web
    subgraph compose[docker compose]
        web["frontend<br/>React + Vite (nginx in production)"]
        api["backend<br/>FastAPI + LangGraph"]
        db[("db<br/>Postgres 16 + pgvector")]
        shop["mock_commerce<br/>Acme order system"]
        evalshop["mock_commerce_eval<br/>evaluation-only shop"]
    end
    web -- "/api (JSON, SSE)" --> api
    api -- "tickets, audit, approvals,<br/>checkpoints, policy vectors" --> db
    api -- "orders, refunds,<br/>replacements, cancellations" --> shop
    api -. "evaluations only:<br/>reset per case, read effects" .-> evalshop
    api -- "structured output" --> llm["LLM provider<br/>(Gemini by default)"]
```

- The **frontend** never talks to the shop or the model. It holds the 15-minute access token in
  memory and renews it with an HttpOnly refresh cookie.
- The **backend** is one process: API, agent runs (background tasks), live events (SSE), and
  in-memory rate limits. See the README's limitations for what that implies.
- The **mock shop** keeps its data in memory and supports idempotency keys and fault injection.
  The evaluation shop is a second instance with admin endpoints; the demo shop has them off.

## Agent graph

```mermaid
flowchart TD
    start((start)) --> intake
    intake -- "injection screen hit,<br/>shop or model failure" --> escalation
    intake -- "order unclear" --> clarify
    intake --> customer_data
    clarify -- "5 questions asked" --> escalation
    clarify --> wait((wait for reply))
    customer_data -- "order not found" --> clarify
    customer_data -- "shop failure" --> escalation
    customer_data -- "someone else's order,<br/>or unknown intent" --> policy_gate
    customer_data --> policy_rag
    policy_rag --> resolution
    resolution -- "model failure" --> escalation
    resolution --> policy_gate
    policy_gate -- auto_approve --> executor
    policy_gate -- needs_approval --> request_approval
    policy_gate -- deny --> notifier
    policy_gate -- escalate --> escalation
    request_approval --> await_approval
    await_approval -- "interrupt(): paused in the<br/>Postgres checkpointer" --> await_approval
    await_approval -- approved --> executor
    await_approval -- rejected --> notifier
    executor -- "blocked or failed" --> escalation
    executor --> notifier
    notifier --> done((end))
    escalation --> done
```

What each step may and may not do:

| Step | Does | Never |
| --- | --- | --- |
| intake | Screens for prompt injection, lists the customer's own orders, classifies the intent, matches the order | Accepts an order the model picked that is not in the customer's list |
| customer_data | Fetches the customer and the order | Decides ownership (the policy gate does, and audits it) |
| policy_rag | Searches the policy documents that govern the intent | Sends the model sections from unrelated documents |
| resolution | Asks the model for one proposed action, SKUs, a reason, and citations | Lets the model set an amount (the schema has no amount field) |
| policy_gate | Applies the business rules in `policy_engine.py` and audits the decision | Calls the model |
| executor | Re-checks the decision and any approval, then calls the tool with a per-ticket idempotency key | Acts without `auto_approve` or an approved approval row |

Routing reads facts from the state; no route asks the model.

## Approval flow

```mermaid
sequenceDiagram
    actor C as Customer
    participant API as Backend
    participant G as Agent graph
    participant DB as Postgres
    participant S as Mock shop
    actor V as Supervisor

    C->>API: "My headphones arrived damaged, I want a refund"
    API->>G: start run (thread = ticket id)
    G->>S: list orders, get customer, get order
    G->>G: policy search, proposal, policy gate ($79.99: needs approval)
    G->>DB: approval row, ticket awaiting_approval, checkpoint
    G-->>C: "I've asked a supervisor to approve a refund of $79.99" (SSE)
    V->>API: approve (row locked: a second click gets 409)
    API->>G: resume (decision read from the database)
    G->>S: POST /refunds (Idempotency-Key: refund-{ticket_id})
    G->>DB: ticket resolved, audit entries
    G-->>C: "I've refunded $79.99 to your card ending 4242 for order ORD-10001" (SSE)
```

## When the agent hands over

Escalated tickets (and open ones the agent is not running) get a "Handle this ticket" panel in
the console. A staff member can reply, act, or close:

```mermaid
flowchart LR
    pick["Refund / replacement / cancel<br/>(items optional)"] --> check["policy_engine.evaluate<br/>plus hard limits"]
    check -- allowed --> act
    check -- "override<br/>(over $500, outside window,<br/>not delivered, fraud flag)" --> reason{"written reason<br/>at least 10 characters?"}
    reason -- yes --> act["agent's audited tools<br/>same idempotency keys<br/>actor = staff member"]
    reason -- no --> refuse422["422: reason required"]
    check -- "blocked<br/>(other customer, second remedy,<br/>unknown item, shop can't)" --> refuse409["409: never allowed"]
    act --> done["ticket resolved, staff message,<br/>live event to the customer"]
```

The ticket row is locked with `FOR NO KEY UPDATE` while a person acts, so two simultaneous
actions cannot both run. `FOR UPDATE` would also block the audit inserts that reference the row
from other connections.

## Evaluation harness

```mermaid
flowchart LR
    cases["evals/dataset/cases.jsonl<br/>80 cases, each with its own shop data"] --> run
    run["run_evals.py / POST /api/evals/run"] --> prep["recreate &lt;db&gt;_eval,<br/>migrate, load policies"]
    prep --> loop
    subgraph loop[for each case]
        reset["POST /admin/reset<br/>(fixture + faults)"] --> agent["the real graph,<br/>new ticket and thread"]
        agent --> approve["supervisor decision<br/>from the case, then resume"]
        approve --> observe["read back: audit log, approvals,<br/>agent runs, GET /admin/state"]
        observe --> score["score against the expectation"]
    end
    loop --> report["evals/reports/&lt;id&gt;.json<br/>docs/eval-report.md<br/>README results block"]
```

- Effects (refunds, replacements, cancellations) are read from the evaluation shop itself, so
  an action counts even if the agent's own record of it were wrong.
- With `--llm mock`, a keyword stand-in replaces the model. CI uses it on the first ten cases to
  check the wiring without calling a paid model; it says nothing about model quality.
- The live model runs at temperature 0 unless it accepts only its own default (reasoning
  models); the report records which. Per-minute rate limits are waited out; a daily quota or an
  account without credit stops the run, and the report is marked incomplete.

## Data model

| Table | Holds |
| --- | --- |
| users | Email, bcrypt hash, role, and for customers the commerce customer id |
| refresh_tokens | Hashed refresh tokens, rotation chain, revocation |
| tickets | Public id (TCK-1000), customer, order, category, status, priority, summary, `customer_replied` |
| messages | The conversation (customer, agent, supervisor, system) |
| approvals | The proposed action, status, decider, note |
| audit_logs | Every step, tool call (including rejected ones), policy decision, and human decision. Append-only: a trigger rejects UPDATE and DELETE |
| agent_runs | Tokens, cost, latency, and status of each run |
| policy_chunks | Policy sections with 384-dimension embeddings |
| checkpoint_* | LangGraph's checkpointer tables (plain JSON state) |
