# OpenWeight frontend

The OpenWeight product interface is a custom React and TypeScript single-page
application in `frontend/`. It presents the existing FastAPI capabilities; it
is not an authorization boundary, agent implementation, database client, or
MCP replacement.

## Product structure

- **Overview** reads liveness, sanitized readiness, and service metadata. It
  never fabricates operational counts.
- **Policy & Evidence** calls the dedicated guarded internal-policy contract.
  Employees can type any internal policy or access-governance question; the
  curated questions are optional editable examples, not an allowlist. The page
  shows real retrieval/generation stages and progressively renders only the
  model's employee-facing final-answer channel. Validated citation controls
  and typed supporting-evidence cards appear only after the authoritative
  terminal response. Filesystem paths, prompts, hidden reasoning, model
  traces, and database internals never enter the browser contract.
  A limited text-only Markdown renderer supports paragraphs, lists, headings,
  bold, and emphasis in answers and evidence; model HTML and executable links
  are never interpreted. After final validation, inline citation references
  and citation chips identify the policy and move keyboard focus to its
  evidence card. The employee-facing Supporting Evidence section includes only
  evidence cited and validated in the answer; uncited retrieval candidates are
  not presented as support. Chunk references remain in evidence details. Citation-ID
  matching does not establish the semantic accuracy of a claim.
  Request correlation IDs stay in API responses and observability but
  are omitted from the normal employee result.
- **Access requests** lists and inspects established fictional records through
  two bounded read-only endpoints. An Operator can create a status proposal;
  proposal creation is explicitly presented as non-executing.
- **Approvals** requires a deliberate approve or reject confirmation. The UI
  waits for backend confirmation and handles conflicts without optimistic
  claims; PostgreSQL checkpoints and the execution ledger remain the
  exactly-once boundary.
- **System** truthfully diagrams React and MCP as two surfaces over the same
  FastAPI security, LangGraph, PostgreSQL/pgvector, and controlled-executor
  core. Azure architecture exists but is not deployed.

The visual system uses native CSS, responsive layouts, semantic HTML, visible
focus treatment, native dialog semantics, text-plus-color status indicators,
and reduced-motion support. Desktop is primary, with navigation and workflows
adapted for tablet and mobile widths.

## Versions and dependency policy

Dependencies are exact-pinned and locked in `package-lock.json`. D16 uses
React 19.2.8, React DOM 19.2.8, React Router DOM 7.18.3, TypeScript 6.0.3,
Vite 8.2.2, Vitest 4.1.11, Testing Library, ESLint, and Lucide React. The
project requires Node 22.12 or newer; the container builder pins Node 22.23.2.
TypeScript 6.0.3 is the newest stable release compatible with the selected
`typescript-eslint` peer range; dependency resolution is not forced.

## Local development

Use a supported Node runtime and install the locked dependency tree:

```bash
cd frontend
npm ci
npm run dev
```

Vite listens on `127.0.0.1:5173` and proxies `/healthz`, `/readyz`, `/v1`,
`/mcp`, and `/metrics` to FastAPI on `127.0.0.1:8000`. Run the API separately
with the frontend server disabled (the default in `.env.example`).

Only public browser configuration belongs in Vite variables:

- `VITE_API_BASE_URL` optionally supplies a credential-free HTTP(S) API base
  URL. Same-origin is the default and the production recommendation.
- `VITE_DEMO_MODE=true` shows the explicitly labelled local Demo Persona
  selector. It changes interface affordances only; the server still validates
  every bearer token and permission.

Never place signing keys, database credentials, Key Vault values, client
secrets, or provider tokens in `VITE_*`: Vite embeds them in public assets.
Bearer tokens are held only in module memory by the API client and are not
written to `localStorage`. Production identity remains the D14 Microsoft
Entra/OIDC seam; D16 does not add a fake password login.

## Quality commands

```bash
cd frontend
npm ci
npm run typecheck
npm run lint
npm test
npm run build
npm audit
```

Vitest and Testing Library cover application rendering, navigation, live
metadata, query loading/success/failure, evidence privacy, persona affordances,
fictional access-request rendering, proposal separation, explicit approval,
rejection, and replay/conflict handling. Tests use fetch fakes and do not load
a model or contact an identity provider.

## Production and container delivery

The Dockerfile is a multi-stage build:

```text
locked Node builder -> frontend/dist -> non-root Python runtime
```

The final process remains `python -m openweight_platform.api.run` as UID/GID
10001. `OPENWEIGHT_FRONTEND_ENABLED=true` and
`OPENWEIGHT_FRONTEND_DIST_DIR=/app/frontend/dist` enable static delivery in
Compose. Compose also supplies the public build argument
`VITE_DEMO_MODE=true`; cloud/release builds retain the Dockerfile's safe
`false` default unless a demo build is intentional. Native API/test startup
leaves static delivery disabled unless explicitly set.

FastAPI registers only the known SPA locations and `/assets`. It does not use
a generic catch-all, so `/mcp`, `/v1/*`, `/healthz`, `/readyz`, `/metrics`,
`/openapi.json`, and `/docs` remain independent routes. This one-container
shape is the intended basis for the later Hugging Face Docker Space, but D16
does not deploy it.

## Security boundary and limitations

The browser hides or disables controls for clarity, but the backend is always
authoritative. The existing role mapping remains Reader (query/read), Approver
(query/read/approval), and Operator (query/read/proposal/approval). Proposal,
durable approval, fixed execution, and transactional idempotency are not
reimplemented in JavaScript.

Client errors map status codes to fixed safe wording. Raw stack traces, token
validation details, database errors, prompts, model responses, hidden
reasoning, and internal traces are not rendered. No generic SQL, arbitrary
write, arbitrary tool, or repository-file capability was added.

Policy queries show distinct company-policy search and grounded-generation
states because local GPT-OSS generation may take tens of seconds. Final-answer
text streams progressively, submissions and example changes are disabled while
a request is active, and an interrupted stream becomes a safe retry state. The
result identifies the source scope as internal policy, asks the employee to
review cited evidence, and renders insufficient evidence as a normal governed
outcome. External web search is not an automatic fallback.

Browser E2E automation is intentionally deferred if a suitable browser is not
already available; component tests plus a real container/browserless smoke are
the D16 automated boundary. D17 owns Hugging Face deployment and D18 owns the
final portfolio README and demo polish.
