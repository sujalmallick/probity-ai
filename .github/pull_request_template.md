## What and why

<!-- What does this change, and why? Link the issue: "Closes #123". -->

## How I tested it

<!-- Commands you ran and tests you added or changed. -->

## Screenshots (UI changes)

<!-- Before / after. Use test data only: no real invoices, names, emails or account numbers. -->

## Checklist

- [ ] Backend tests pass: `cd apps/api && python -m pytest -q`
- [ ] Frontend checks pass (if I touched `apps/web`): `npm run typecheck` and `npm run build`
- [ ] I added or updated tests for the behaviour I changed
- [ ] No secrets, `.env` contents, real invoices or personal data in the diff
- [ ] Wording rules: "anomaly" / "recommend hold" / "could not verify", never "fraud", "scam" or "fake"
- [ ] The risk engine still takes no AI or text input
- [ ] Missing data says "could not verify" and never invents a result
- [ ] New migrations (if any) have a working `downgrade()`
- [ ] Docs updated if setup, settings or behaviour changed
