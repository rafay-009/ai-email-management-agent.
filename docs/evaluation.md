# Evaluation

The deterministic adapter was evaluated on 24 synthetic messages in `backend/app/fixtures/demo.json`.

| Metric | Observed |
| --- | --- |
| Category accuracy | 24/24 |
| Reply-required precision | 17/17 |
| Reply-required recall | 17/17 |
| Priority band | 24/24 |
| Task presence | 22/24 |

The fixture is small and guided rule development. The perfect category score reflects these examples. The task metric measures presence only. Confidence is uncalibrated. There were no live OpenAI calls or live Gmail operations. Token usage is recorded when a provider supplies it.

The pytest suite checks scoring, structured schema validation, ambiguous dates, MIME conversion, reply address constraints, malicious message handling, owner isolation, edited-payload approval, CSRF, draft supersession, task deduplication, rejection, missing Gmail scopes, duplicate approval, uncertain outcomes, disconnect and local deletion. Playwright covers the review workflow and mobile inbox.

Local verification passed 11 pytest tests and two Chromium tests. The suite includes mocked OAuth token exchange, Gmail read retries and write timeout, and OpenAI prompt roles, evidence validation, and retry. SQLite migration upgrade, schema check, downgrade and re-upgrade passed. Ruff, ESLint, TypeScript, and the Next.js production build passed.
