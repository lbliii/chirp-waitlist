CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY,
    product_name TEXT NOT NULL,
    headline TEXT NOT NULL,
    subhead TEXT NOT NULL,
    privacy_text TEXT NOT NULL,
    accent TEXT NOT NULL,
    retention_days INTEGER NOT NULL
);

INSERT INTO settings (
    id, product_name, headline, subhead, privacy_text, accent, retention_days
) VALUES (
    1,
    'Northstar',
    'Join us before launch day.',
    'Get a place in line, invite people you trust, and watch the launch get closer.',
    'We use your details only for this launch. You can ask the owner to remove them at any time.',
    'coral',
    365
) ON CONFLICT(id) DO NOTHING;

CREATE TABLE IF NOT EXISTS signups (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    email_normalized TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'direct',
    referral_code TEXT NOT NULL UNIQUE,
    referred_by TEXT REFERENCES signups(id) ON DELETE SET NULL,
    cohort TEXT NOT NULL DEFAULT '',
    invite_state TEXT NOT NULL DEFAULT 'waiting',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS signups_created_idx ON signups(created_at DESC);
CREATE INDEX IF NOT EXISTS signups_referrer_idx ON signups(referred_by);
CREATE INDEX IF NOT EXISTS signups_invite_idx ON signups(invite_state, created_at DESC);
