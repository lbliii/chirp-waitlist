# Deploy and Host a Referral Waitlist with Railway

Deploy Chirp Waitlist as a polished launch page and private signup desk. One
click provisions the web application and PostgreSQL with generated credentials,
private service wiring, and no required configuration fields.

## About Hosting Chirp Waitlist

Chirp Waitlist runs as one Chirp web service backed by Railway PostgreSQL. The
public page collects duplicate-safe signups, gives new participants a
browser-held referral link, and shows live aggregate activity. The private owner
desk provides search, cohorts, invite states, retention, deletion, theming, and
formula-safe CSV export.

## Why Deploy Chirp Waitlist on Railway

- Zero-input deployment with generated owner and signing secrets.
- Branded launch page with three restrained theme accents and editable copy.
- Duplicate-safe intake and enumeration-resistant responses.
- Direct referral attribution without tracking pixels or analytics vendors.
- Live HTMX aggregate refresh with an accessible no-JavaScript form path.
- One focused application service—no Redis, worker, email vendor, or CRM.

## Common Use Cases

- Pre-launch signup pages for indie products and small startups.
- Referral-driven private betas and founding-member cohorts.
- Event, community, or open-source project interest lists.
- A portable lead list that can be ejected, inspected, and fully owned.
- A simple launch page before the main product or marketing site exists.

## Dependencies for Chirp Waitlist Hosting

The template uses only the included Chirp application and Railway PostgreSQL.
No email provider, queue, cache, object store, CRM, or analytics service is
needed.

### Deployment Dependencies

- Python 3.14 via Railpack
- Chirp 0.10.x with PostgreSQL and signed-session extras
- Railway PostgreSQL with a persistent data volume
- Generated `CHIRP_SECRET_KEY` and `WAITLIST_ADMIN_TOKEN`
- Private `${{Postgres.DATABASE_URL}}` service reference
