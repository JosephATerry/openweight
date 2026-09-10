# Public CI/CD

GitHub is the canonical engineering repository: it contains the application,
tests, documentation, Docker configuration, and Azure reference architecture.
The Hugging Face Space repository is a generated deployment artifact containing
only the files needed to run the recruiter demo. Development does not occur in
the Space repository. The intended GitHub repository is
`JosephATerry/openweight`; its deployment destination is the distinct Hugging
Face namespace `josephaterry/openweight`.

The workflows become active when this sanitized repository is published to
GitHub. Deployment authentication uses the repo-level Hugging Face Trusted
Publisher already configured for this workflow; GitHub stores no Hugging Face
deployment secret. The workflows do not invoke GPT-OSS or require an inference
credential.

## Continuous integration

`.github/workflows/ci.yml` runs on every pull request, every push to `main`,
and explicit manual dispatch. The workflow token has only `contents: read`.
External actions are pinned to immutable commit SHAs. The quality job uses
Python 3.12.11 and Node 22.23.2 to run:

- the model-free, non-holdout pytest selection;
- dependency consistency and vulnerability checks;
- the locked frontend install, TypeScript check, lint, Vitest suite, build,
  and npm audit;
- a secret scan over only the files tracked by this sanitized repository;
- deterministic Hugging Face Space export validation.

The public repository intentionally has no `data/evals/`, `data/training/`, or
`mlruns/` tree. CI nevertheless names the holdout test in pytest's explicit
ignore list so the safety boundary remains visible and fails closed if that
test is ever reintroduced accidentally. Offline environment settings and an
empty CUDA device selection prevent model downloads and GPU use.

After quality passes, the container job validates Compose, builds the
production image, and checks only non-inference endpoints in an isolated
API/PostgreSQL environment. Cleanup is scoped to the CI Compose project. It
does not query GPT-OSS, build an embedding index, or execute a governed write.
Terraform's source-level configuration checks remain in the model-free pytest
suite; CI never runs `terraform plan` or `terraform apply`.

## Continuous deployment

`.github/workflows/deploy-huggingface.yml` is triggered by completion of the
workflow named `CI`, but its deploy job runs only when all of these conditions
hold:

1. CI concluded successfully;
2. CI was triggered by a push, not a pull request or manual dispatch;
3. the triggering branch is `main`;
4. the triggering repository is this repository, not a fork;
5. the triggering commit is still the current `main` commit.

The deployment workflow does not consume artifacts or caches produced by
untrusted pull-request code. It checks out the exact successful main commit,
recreates the Space export from that trusted source, and synchronizes the
export to `josephaterry/openweight`. Deployment runs are serialized under one
`hugging-face-production` concurrency group; a newer main deployment cancels
an obsolete in-progress run.

Configure the GitHub Environment `hugging-face-production` with deployment
branch protection for `main`. A required reviewer is recommended for the first
deployment and whenever manual approval is desired. Environment protection
remains useful as an explicit release boundary; it stores no Hugging Face
deployment credential.

Deployment uses Hugging Face repo-level Trusted Publishing with these exact
identity claims:

```text
provider: GitHub Actions
repository: JosephATerry/openweight
ref: refs/heads/main
workflow: deploy-huggingface.yml
resource: spaces/josephaterry/openweight
```

The deployment job grants `contents: read` and `id-token: write`. During the
upload step, `HF_OIDC_RESOURCE=spaces/josephaterry/openweight` instructs
`huggingface_hub==1.27.0` to obtain a GitHub OIDC identity token, exchange it
with Hugging Face, and use the returned short-lived token directly for the
upload. Hugging Face verifies the repository, ref, and workflow claims against
the configured publisher. The resulting token is scoped to this Space and
expires after approximately one hour; it is not printed, stored, or exported by
the workflow.

The live Space's runtime `HF_TOKEN` remains a separate inference-only secret in
Hugging Face settings. It is never exposed to GitHub Actions. A repo-level
Trusted Publisher token writes only its configured repository and cannot call
Inference Providers.

## Deterministic Space export

`scripts/export_huggingface_space.py` constructs an export from a fixed
allowlist. It maps `deploy/huggingface/README.template.md` to the Space root
`README.md`, preserving the required Docker Space metadata, and includes only:

- `.dockerignore`, `Dockerfile`, and runtime Python requirements;
- the React source/build inputs;
- application Python source;
- the synthetic policy corpus under `data/policies/`;
- the portable policy index and metadata;
- the three setup/index scripts copied by the Dockerfile;
- the container health-check script.

The exporter never traverses `data/evals/` or `data/training/`. It rejects
symlinks, a nonempty output directory, unexpected output files, and any path
outside its allowlist. `.git`, GitHub workflows, tests, docs, local environments,
models, caches, MLflow data, generated frontend assets, browser output,
credentials, databases, Terraform state, and result artifacts do not enter the
Space export.

`scripts/deploy_huggingface_space.py` validates the export again, fixes the
destination to `josephaterry/openweight`, and gives the Space commit the source
GitHub SHA. `--delete '*'` makes the remote deployment tree match the current
allowlist rather than retaining stale files. The script has a token-free
`--dry-run` mode for command inspection.

Generate an export locally outside the repository:

```bash
python scripts/export_huggingface_space.py --output /tmp/openweight-space
python scripts/deploy_huggingface_space.py \
  --export-dir /tmp/openweight-space \
  --source-sha 0000000000000000000000000000000000000000 \
  --dry-run
```

These commands do not invoke model inference. A real upload occurs only when
`--dry-run` is omitted inside the matching GitHub Actions OIDC context with the
exact `HF_OIDC_RESOURCE` configured.

## Rollback and cost boundary

To redeploy or roll back, move `main` forward with a reviewed commit (a revert
commit for rollback) and let successful CI trigger a new deterministic Space
deployment. Do not edit the generated Space repository as the normal release
path.

CI/CD builds and uploads source but does not intentionally call GPT-OSS. It
does not change Hugging Face hardware, billing, inference-provider settings, or
the Space runtime secret. Runtime inference remains metered separately under
the Space's existing `HF_TOKEN` and account settings.

## Local parity

Run the public CI-equivalent checks from a clean environment:

```bash
CUDA_VISIBLE_DEVICES='' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  PYTHONPATH=src .venv/bin/python -m pytest -q \
  --ignore=tests/test_policy_dataset_v3.py
.venv/bin/python -m pip check
cd frontend
npm ci
npm run typecheck
npm run lint
npm test
npm run build
npm audit --audit-level=high
cd ..
pip-audit --requirement requirements-container.txt --progress-spinner off
git ls-files -z | xargs -0 detect-secrets-hook --baseline .secrets.baseline
docker compose config --quiet
git diff --check
```
