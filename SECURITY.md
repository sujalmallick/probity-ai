# Security policy

## Status: hackathon prototype

Probity is a **student hackathon prototype**. It has had internal security reviews (see `docs/Security.md` and
`docs/SECURITY_HANDOVER.md`), but **it has not been independently audited and isn't ready for production use** with real company
payments. Some items are still open, for example automatic deletion after the retention period, and supply-chain pinning.

## Reporting a vulnerability

**Please don't open a public issue, pull request or discussion for a security problem.**

Report it privately through GitHub: on the repository page, go to **Security → Report a vulnerability** (private vulnerability
reporting). Include:

- what the problem is and where (file, endpoint or page)
- steps to reproduce, using test data only
- what an attacker could do with it

Never include real API keys, real invoices or other people's personal data in a report. If you found a leaked key, say where; don't copy it.

We'll acknowledge your report as soon as we can (this is a volunteer project, so please allow a few days), keep you updated, and credit
you if you'd like.

## Testing safely

Test against **your own local copy** (see [docs/SETUP.md](docs/SETUP.md)), not the live site at https://probity-3xgk.onrender.com.
Real people use it, it runs on a small free plan, and automated scanning or load testing can take it down. If a problem can only be
shown on the live site, describe it in your private report instead of exploiting it.

## In scope

- Getting into another workspace's data (workspace isolation / row-level security)
- Bypassing sign-in, roles, two-factor checks or two-approver rules
- Making an invoice auto-clear when it shouldn't, or changing a risk score through invoice text, web pages, vendor replies or AI output
- Prompt injection that changes what Probity does
- Server-side request forgery through the web fetcher, file-upload attacks, or parser crashes
- Leaking bank account numbers, keys or other secrets (logs, exports, API responses, error messages)
- Tampering with the audit log without detection
- Sending email to addresses outside the allowlist while testing

## Out of scope

- Problems in third-party services themselves (Clerk, Anthropic, Google Gemini, Tavily, Resend, Cloudflare). Report those to the provider.
- Attacks that need someone's own keys or `.env` file, or full access to their computer.
- The default local-development database passwords used by `infra/docker-compose.dev.yml` and `scripts/dev.ps1` (they're for a database on your own computer only).
- Denial of service through very high traffic.

## If you leak your own key

Rotate it at the provider straight away. See "Key safety" in [docs/API_KEYS.md](docs/API_KEYS.md#key-safety).
