# Probity docs
*Evidence before payment.*

## Current (match the code)

| Doc | What it's for |
|---|---|
| [SETUP.md](SETUP.md) | Install and run Probity, step by step, with troubleshooting |
| [API_KEYS.md](API_KEYS.md) | Every key and service: required or optional, where it goes |
| [FEATURES.md](FEATURES.md) | What works, what's partial, what's planned |
| [Architecture.md](Architecture.md) | How the code is organised and how a case flows |
| [API_CONTRACT.md](API_CONTRACT.md) | The API the web app uses, with a changelog |
| [Guardrails.md](Guardrails.md) | The safety rules and how they're enforced |
| [DECISIONS.md](DECISIONS.md) | Design decisions and current limits |
| [SECURITY_HANDOVER.md](SECURITY_HANDOVER.md) | Security fixes, the tests that guard them, open items |
| [FAILURE_AUDIT.md](FAILURE_AUDIT.md) | Failure handling: stuck cases, retries, the error format |
| [WEB_REAL_DATA_CHECKLIST.md](WEB_REAL_DATA_CHECKLIST.md) | The web app's move to real data (done) |
| [openapi.json](openapi.json) | Generated API schema (`python -m probity.api.dump_openapi`) |

Also: [../infra/cloudflare/README.md](../infra/cloudflare/README.md) (deployment) and
[../benchmark/real/README.md](../benchmark/real/README.md) (testing on your own invoices).

## Original design docs (history)

Written before the build. They describe the first demo version (seeded data, offline modes, a synthetic benchmark), which no longer
exists. Kept for background; where they disagree with the docs above, the docs above are right.

[PRD.md](PRD.md) · [Feature.md](Feature.md) · [API.md](API.md) · [Security.md](Security.md) · [AI_Instructions.md](AI_Instructions.md) ·
[AI_Infrastructure.md](AI_Infrastructure.md) · [TechStack.md](TechStack.md) · [UIUX.md](UIUX.md) · [REFERENCE_MAP.md](REFERENCE_MAP.md) ·
[MASTER_PROMPT.md](MASTER_PROMPT.md)
