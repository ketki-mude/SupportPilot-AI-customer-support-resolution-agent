# SupportPilot AI

**SupportPilot AI** is a customer support resolution agent that can handle routine commerce-related requests end to end. It combines agentic AI workflows, policy-aware decision making, human approval for high-risk actions, and full auditability.

The system can classify a customer request, identify the relevant order, retrieve and evaluate policy rules, propose the correct action, pause for supervisor approval when required, execute the action through a commerce API, and respond back to the customer.

The current version includes a messenger-style customer chat, a three-pane staff console, approval workflows, and an audit log with CSV export.

## Key Capabilities

- End-to-end customer support resolution
- LLM-based intent classification and reasoning
- Order lookup and commerce API integration
- Policy retrieval and validation
- Human-in-the-loop approval for risky actions
- Idempotent actions to prevent duplicate refunds
- Real-time agent progress updates
- Role-based access for customers, agents, supervisors, and admins
- Audit logging and CSV export
- Failure simulation for testing retries and resilience
- Dockerized local development environment
- Automated backend, frontend, lint, and end-to-end tests

## Example Workflow

A customer can submit a request such as:

> "My headphones arrived damaged, I want a refund."

The agent then:

1. Classifies the request.
2. Finds the matching customer order.
3. Retrieves the relevant refund policy.
4. Evaluates whether the request satisfies policy requirements.
5. Determines whether supervisor approval is required.
6. Pauses the workflow when approval is needed.
7. Executes the refund after approval.
8. Sends the final confirmation back to the customer.
9. Records the entire workflow in the audit log.

For example, a refund above the configured `$50` threshold is automatically routed to a supervisor instead of being executed autonomously.

## Architecture

The application consists of:

- **Frontend:** Customer chat and internal support console
- **Backend API:** Agent orchestration, authentication, approvals, policies, and ticket management
- **Mock Commerce API:** Orders, refunds, and simulated external-system failures
- **PostgreSQL:** Users, tickets, approvals, agent state, and audit records
- **LLM Layer:** Request understanding, reasoning, and structured decision support
- **Policy Retrieval:** Embedded support policies used during agent decision making
- **Human Approval Layer:** Supervisor review for actions outside safe autonomy limits

The agent requires a `GOOGLE_API_KEY` by default, although other model providers can be configured through `LLM_PROVIDER`.

## Run Locally

Docker with Compose v2 is required.

```sh
cp .env.example .env
