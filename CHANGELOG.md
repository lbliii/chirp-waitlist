# Changelog

## 0.1.2 — 2026-07-15

- Record the published Railway template, canonical demo, and latest successful
  clean-deployment smoke in source collateral.

## 0.1.1 — 2026-07-15

- Make new-signup, owner-update, deletion, and retention confirmation portable
  across database drivers that do not return affected-row counts.
- Verify the inserted signup identity before issuing its browser-held referral
  pass, preserving enumeration resistance under duplicate and concurrent intake.

## 0.1.0 — 2026-07-15

- Ship a branded public waitlist with plain and HTMX signup paths, live
  aggregate refresh, and browser-held referral links.
- Add normalized duplicate handling, enumeration-resistant responses, bounded
  direct referral attribution, a honeypot, burst limits, and a 16 KiB body cap.
- Add owner authentication, search, cohorts, invite states, deletion, retention,
  theme and copy settings, and spreadsheet-formula-safe CSV export.
- Add strict security headers, health/readiness, PostgreSQL migrations, a real
  favicon, and a responsive launch-control visual system.
- Add zero-input Railway collateral and issue-tagged behavioral coverage for
  Chirp issue #823.
