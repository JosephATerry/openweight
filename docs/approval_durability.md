# Durable approvals and controlled-write idempotency

## Runtime architecture

`OPENWEIGHT_CHECKPOINT_BACKEND` selects `memory` or `postgres`.

- `memory` is the local/test default and is intentionally process-local.
- `postgres` opens LangGraph `PostgresSaver` and a PostgreSQL approval-session
  store. Initialization failure is fatal for approval operations; the service
  never silently falls back to memory. Readiness verifies the checkpoint,
  approval-session, and execution-ledger tables.

The PostgreSQL saver uses strict msgpack deserialization with an explicit
allowlist for the two application approval models. `setup_security.py` calls
the upstream saver migration API; application startup does not run privileged
DDL.

## Proposal and resume flow

1. The fixed access-request proposal is validated.
2. A durable `security.approval_sessions` row is inserted. A partial unique
   index permits only one pending proposal for a target request.
3. LangGraph checkpoints the proposal and pauses at the approval interrupt.
4. Resume locks the session row with `SELECT ... FOR UPDATE`, so concurrent
   processes serialize the decision.
5. Rejection records a terminal session outcome and performs no write.
6. Approval invokes only the fixed parameterized executor with the approval ID
   as its deterministic effect ID.

The session row and LangGraph checkpoint are separate writes. Proposal creation
deletes the pending session if checkpoint creation fails. During resume, the
effect ledger below makes retry safe if the controlled write commits but the
session terminal update is interrupted.

## Transactional effect ledger

`security.execution_ledger.effect_id` is unique. Within one PostgreSQL
transaction, the action service:

1. claims the effect with `INSERT ... ON CONFLICT DO NOTHING`;
2. if newly claimed, locks and updates the exact
   `operations.access_requests` row;
3. writes the structured result and terminal ledger state;
4. commits all three changes together.

On a duplicate, PostgreSQL conflict/row-lock behavior waits for the concurrent
transaction and then returns the already stored result. Reusing an effect ID
for different arguments or encountering a nonterminal committed claim fails
closed. A write failure rolls back both the operational change and the claim;
there is no durable "completed" marker before the effect.

This is effect exactly-once for the one database status mutation under the
same PostgreSQL authority. It is not a generic distributed transaction system
and does not authorize arbitrary tools, SQL, tables, columns, or arguments.

## Bootstrap and restart behavior

Run the migration boundary before selecting PostgreSQL durability:

```bash
PYTHONPATH=src .venv/bin/python scripts/setup_security.py
```

For a separate existing runtime role, an administrator may additionally set
`POSTGRES_APPLICATION_ROLE`. The role password itself is supplied and rotated
outside this script.

A pending PostgreSQL-configured proposal can be resumed after API restart, and
multiple processes address the same checkpoint by the stable approval/thread
ID. Duplicate resumes serialize on the durable session row; the loser receives
a conflict and cannot repeat the effect. Local `memory` mode retains the old
single-process behavior for deterministic tests.

The isolated PostgreSQL smoke additionally exercises a runtime restart,
sequential replay, two concurrent resume attempts, and rejection. It requires
fictional seed data and never invokes a model or policy index.

## Scaling posture

The implementation removes the prior process-local checkpoint and duplicate
effect dependency in PostgreSQL mode. Terraform nevertheless keeps
`min_replicas=1` and `max_replicas=1` as a conservative reference default until
the actual Azure database bootstrap, connection capacity, migration rollout,
failure injection, and multi-replica load behavior are verified. Scale-to-zero
also remains disabled to avoid approval latency and cold-start surprises.
