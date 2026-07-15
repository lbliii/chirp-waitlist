# Chirp Waitlist

A focused, self-hosted launch waitlist with direct referral attribution, built
with [Chirp](https://lbliii.github.io/chirp/) and PostgreSQL.

Chirp Waitlist gives an early product a branded signup page, generated referral
links, live aggregate counts, and a private launch desk without adding a mailing
platform, CRM, tracking pixel, or client-side application. Owners can search,
cohort, invite, delete, retain, and safely export their launch data.

## Deploy on Railway

The public Railway template will provision one `web` service and official
Railway PostgreSQL. Deployment is zero-input: Railway generates
`CHIRP_SECRET_KEY` and `WAITLIST_ADMIN_TOKEN`, then wires the private
`${{Postgres.DATABASE_URL}}` reference automatically.

After deployment, copy `WAITLIST_ADMIN_TOKEN` from the `web` service variables
and unlock the owner launch desk. Never expose the token in page source,
screenshots, logs, or public support requests.

## Local development

Chirp Waitlist requires Python 3.14.

```bash
uv sync
uv run python app.py
```

Development defaults to SQLite and the local owner token
`waitlist-local-admin`. Production fails loudly unless the platform supplies the
owner token, signing key, and database connection.

## Contracts

- Plain HTML and HTMX signups share one handler and one template.
- Normalized email uniqueness makes repeated signups safe under concurrency.
- Duplicate responses do not reveal list membership or rewrite attribution.
- Referral links are browser-held capabilities; the app does not claim email
  ownership without a verification channel.
- Direct attribution is bounded to one referrer and never creates referral
  chains that rewrite existing records.
- Request bodies are capped at 16 KiB; a honeypot and in-memory burst limit
  bound public intake without storing raw client IP addresses.
- Owner search, cohorts, invite states, deletion, retention, and CSV export are
  backed by PostgreSQL. Spreadsheet formula cells are neutralized.
- `/health`, `/ready`, CSS, and the linked SVG favicon are deployment gates.

Run the issue-tagged suite with:

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format . --check
```

## Support

Open an issue at <https://github.com/lbliii/chirp-waitlist/issues>. Include the
failing route, deployment timestamp, and redacted logs; never include the owner
token, session cookies, signup data, or database URLs.

## License

MIT
