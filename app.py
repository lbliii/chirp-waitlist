"""Chirp Waitlist: a referral-aware launch list for small products."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import os
import re
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from chirp import OOB, App, AppConfig, Fragment, MutationResult, Page, Request, Response
from chirp.middleware.csrf import CSRFConfig
from chirp.middleware.security_headers import SecurityHeadersConfig
from chirp.middleware.sessions import get_session
from chirp.middleware.stack import secure_stack

ROOT = Path(__file__).parent
MIGRATIONS = ROOT / "migrations"
MAX_BODY = 16 * 1024
MAX_EMAIL = 254
MAX_NAME = 80
MAX_SOURCE = 80
MAX_COHORT = 40
RATE_LIMIT = 12
RATE_WINDOW_SECONDS = 60.0
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_REFERRAL_RE = re.compile(r"^[A-Za-z0-9_-]{8,32}$")
_COHORT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,39}$")
_ACCENTS = {"coral", "lime", "blue"}
_INVITE_STATES = {"waiting", "invited", "joined"}


@dataclass(frozen=True, slots=True)
class WaitlistSettings:
    product_name: str
    headline: str
    subhead: str
    privacy_text: str
    accent: str
    retention_days: int


@dataclass(frozen=True, slots=True)
class WaitlistStats:
    total: int
    invited: int
    referred: int


@dataclass(frozen=True, slots=True)
class Signup:
    id: str
    email: str
    name: str
    source: str
    referral_code: str
    referred_by: str | None
    cohort: str
    invite_state: str
    created_at: str
    direct_referrals: int


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _csv_safe(value: str) -> str:
    return f"'{value}" if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


def _normalize_email(value: str) -> tuple[str, str] | None:
    email = value.strip()
    if len(email) > MAX_EMAIL or not _EMAIL_RE.fullmatch(email):
        return None
    local, domain = email.rsplit("@", 1)
    return email, f"{local}@{domain.lower()}".casefold()


def create_app(
    database_url: str | None = None,
    *,
    admin_token: str | None = None,
    secret_key: str | None = None,
) -> App:
    """Build an isolated Chirp Waitlist application for production or tests."""

    config = AppConfig.from_env(
        template_dir=ROOT / "templates",
        worker_mode="async",
        workers=1,
        htmx=True,
        csp_nonce_enabled=True,
    )
    config = replace(config, max_request_body_size=MAX_BODY)
    if secret_key:
        config = replace(config, secret_key=secret_key)
    if not config.secret_key:
        config = replace(config, secret_key="waitlist-local-signing-key")

    resolved_admin_token = admin_token or os.environ.get("WAITLIST_ADMIN_TOKEN")
    if not resolved_admin_token:
        if config.env != "development":
            raise RuntimeError("WAITLIST_ADMIN_TOKEN is required outside development")
        resolved_admin_token = "waitlist-local-admin"

    resolved_database_url = database_url or os.environ.get(
        "DATABASE_URL", f"sqlite:///{ROOT / 'waitlist.db'}"
    )
    application = App(config, db=resolved_database_url, migrations=str(MIGRATIONS))
    for middleware in secure_stack(
        application.config,
        csrf=CSRFConfig(),
        headers=SecurityHeadersConfig(content_security_policy=None),
    ):
        application.add_middleware(middleware)

    rate_windows: dict[str, deque[float]] = defaultdict(deque)

    def is_admin() -> bool:
        return get_session().get("waitlist_admin") is True

    async def settings() -> WaitlistSettings:
        current = await application.db.fetch_one(
            WaitlistSettings,
            "SELECT product_name, headline, subhead, privacy_text, accent, retention_days "
            "FROM settings WHERE id = 1",
        )
        if current is None:
            raise RuntimeError("Waitlist settings migration did not create the singleton row")
        return current

    async def stats() -> dict[str, int]:
        row = await application.db.fetch_one(
            WaitlistStats,
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(CASE WHEN invite_state = 'invited' THEN 1 ELSE 0 END), 0) AS invited, "
            "COALESCE(SUM(CASE WHEN referred_by IS NOT NULL THEN 1 ELSE 0 END), 0) AS referred "
            "FROM signups",
        )
        if row is None:
            return {"total": 0, "invited": 0, "referred": 0}
        return {"total": int(row.total), "invited": int(row.invited), "referred": int(row.referred)}

    @application.on_startup
    async def validate_schema() -> None:
        await settings()
        await stats()

    async def signups(search: str) -> list[Signup]:
        like = f"%{search.casefold()}%"
        return await application.db.fetch(
            Signup,
            "SELECT s.id, s.email, s.name, s.source, s.referral_code, s.referred_by, "
            "s.cohort, s.invite_state, s.created_at, "
            "(SELECT COUNT(*) FROM signups r WHERE r.referred_by = s.id) AS direct_referrals "
            "FROM signups s WHERE (? = '' OR LOWER(s.email) LIKE ? OR LOWER(s.name) LIKE ? "
            "OR LOWER(s.source) LIKE ? OR LOWER(s.cohort) LIKE ?) "
            "ORDER BY s.created_at DESC",
            search,
            like,
            like,
            like,
            like,
        )

    async def context(
        *,
        search: str = "",
        referral: str = "",
        notice: str = "",
        error: bool = False,
    ) -> dict[str, Any]:
        admin = is_admin()
        personal_referral = ""
        owned_signup_id = get_session().get("waitlist_signup_id")
        if isinstance(owned_signup_id, str):
            personal_referral = str(
                await application.db.fetch_val(
                    "SELECT referral_code FROM signups WHERE id = ?", owned_signup_id
                )
                or ""
            )
        return {
            "admin": admin,
            "error": error,
            "notice": notice,
            "personal_referral": personal_referral,
            "public_base": (
                f"https://{os.environ['RAILWAY_PUBLIC_DOMAIN']}"
                if os.environ.get("RAILWAY_PUBLIC_DOMAIN")
                else "http://localhost:8000"
            ),
            "referral": referral if _REFERRAL_RE.fullmatch(referral) else "",
            "search": search,
            "settings": await settings(),
            "signups": await signups(search) if admin else [],
            "stats": await stats(),
        }

    async def owner_result(notice: str, *, search: str = "") -> MutationResult:
        current = await context(search=search, notice=notice)
        return MutationResult(
            "/#owner",
            Fragment("index.html", "owner_panel", **current),
            Fragment("index.html", "stats", target="live-stats", **current),
            Fragment("index.html", "notice", target="notice", **current),
            trigger="waitlistChanged",
        )

    async def join_result(notice: str, *, referral: str = "", error: bool = False):
        current = await context(referral=referral, notice=notice, error=error)
        return MutationResult(
            "/?joined=1",
            Fragment("index.html", "join_panel", **current),
            Fragment("index.html", "stats", target="live-stats", **current),
            Fragment("index.html", "notice", target="notice", **current),
            trigger="waitlistChanged",
        )

    def rate_key(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "unknown").split(",", 1)[0].strip()
        secret = str(application.config.secret_key).encode()
        return hmac.new(secret, forwarded.encode(), hashlib.sha256).hexdigest()[:24]

    def rate_allowed(key: str) -> bool:
        now = time.monotonic()
        window = rate_windows[key]
        while window and now - window[0] >= RATE_WINDOW_SECONDS:
            window.popleft()
        if len(window) >= RATE_LIMIT:
            return False
        window.append(now)
        return True

    @application.route("/", name="home")
    async def index(request: Request) -> Page | OOB:
        search = (request.query.get("q", "") or "")[:120]
        referral = (request.query.get("ref", "") or "")[:32]
        joined = request.query.get("joined", "") == "1"
        current = await context(
            search=search,
            referral=referral,
            notice="You are on the list. Save this page and share your referral link."
            if joined
            else "",
        )
        if request.is_narrow_fragment:
            return OOB(
                Fragment("index.html", "owner_panel", **current),
                Fragment("index.html", "stats", target="live-stats", **current),
            )
        return Page("index.html", "join_panel", page_block_name="page_root", **current)

    @application.route("/stats", name="stats")
    async def live_stats() -> Fragment:
        return Fragment("index.html", "stats", **(await context()))

    @application.route("/join", methods=["POST"], name="join")
    async def join(request: Request):
        try:
            form = await request.form()
        except UnicodeDecodeError, ValueError:
            return await join_result("That signup could not be read. Please try again.", error=True)
        referral = str(form.get("ref") or "")[:32]
        if not rate_allowed(rate_key(request)):
            return Response(
                "Too many signup attempts. Please wait a minute and try again.",
                status=429,
                content_type="text/plain; charset=utf-8",
                headers=(("Cache-Control", "no-store"),),
            )
        if str(form.get("company") or "").strip():
            return await join_result(
                "You are on the list. We will share launch updates here.", referral=referral
            )

        normalized = _normalize_email(str(form.get("email") or ""))
        name = str(form.get("name") or "").strip()
        source = str(form.get("source") or "direct").strip() or "direct"
        if normalized is None:
            return await join_result(
                "Enter a complete email address.", referral=referral, error=True
            )
        if len(name) > MAX_NAME:
            return await join_result(
                f"Keep the name under {MAX_NAME + 1} characters.", referral=referral, error=True
            )
        if len(source) > MAX_SOURCE:
            return await join_result(
                f"Keep the source under {MAX_SOURCE + 1} characters.", referral=referral, error=True
            )
        email, email_normalized = normalized
        safe_referral = referral if _REFERRAL_RE.fullmatch(referral) else ""
        signup_id = uuid4().hex
        changed = await application.db.execute(
            "INSERT INTO signups "
            "(id, email, email_normalized, name, source, referral_code, referred_by, cohort, "
            "invite_state, created_at) VALUES (?, ?, ?, ?, ?, ?, "
            "(SELECT id FROM signups WHERE referral_code = ?), '', 'waiting', ?) "
            "ON CONFLICT(email_normalized) DO NOTHING",
            signup_id,
            email,
            email_normalized,
            name,
            source,
            secrets.token_urlsafe(9),
            safe_referral,
            _now(),
        )
        if changed:
            get_session()["waitlist_signup_id"] = signup_id
        return await join_result(
            "You are on the list. We will share launch updates here.", referral=referral
        )

    @application.route("/admin/login", methods=["POST"], name="admin.login")
    async def admin_login(request: Request) -> MutationResult:
        form = await request.form()
        if hmac.compare_digest(str(form.get("token") or ""), resolved_admin_token):
            get_session()["waitlist_admin"] = True
            return await owner_result("Launch desk unlocked for this browser.")
        return await owner_result("That owner token was not accepted.")

    @application.route("/admin/logout", methods=["POST"], name="admin.logout")
    async def admin_logout() -> MutationResult:
        get_session().pop("waitlist_admin", None)
        return await owner_result("Launch desk locked.")

    @application.route("/admin/signups/{signup_id}", methods=["POST"], name="admin.signups.update")
    async def update_signup(signup_id: str, request: Request) -> MutationResult:
        if not is_admin():
            return await owner_result("Unlock the launch desk before changing signups.")
        form = await request.form()
        cohort = str(form.get("cohort") or "").strip()
        invite_state = str(form.get("invite_state") or "")
        if cohort and not _COHORT_RE.fullmatch(cohort):
            return await owner_result(
                "Cohorts use up to 40 letters, numbers, spaces, dots, or dashes."
            )
        if invite_state not in _INVITE_STATES:
            return await owner_result("Choose a supported invite state.")
        changed = await application.db.execute(
            "UPDATE signups SET cohort = ?, invite_state = ? WHERE id = ?",
            cohort,
            invite_state,
            signup_id,
        )
        return await owner_result("Signup updated." if changed else "Signup not found.")

    @application.route(
        "/admin/signups/{signup_id}/delete", methods=["POST"], name="admin.signups.delete"
    )
    async def delete_signup(signup_id: str) -> MutationResult:
        if not is_admin():
            return await owner_result("Unlock the launch desk before deleting signups.")
        await application.db.execute(
            "UPDATE signups SET referred_by = NULL WHERE referred_by = ?", signup_id
        )
        changed = await application.db.execute("DELETE FROM signups WHERE id = ?", signup_id)
        return await owner_result("Signup deleted." if changed else "Signup not found.")

    @application.route("/admin/settings", methods=["POST"], name="admin.settings")
    async def update_settings(request: Request) -> MutationResult:
        if not is_admin():
            return await owner_result("Unlock the launch desk before changing branding.")
        form = await request.form()
        product_name = str(form.get("product_name") or "").strip()
        headline = str(form.get("headline") or "").strip()
        subhead = str(form.get("subhead") or "").strip()
        privacy_text = str(form.get("privacy_text") or "").strip()
        accent = str(form.get("accent") or "")
        try:
            retention_days = int(str(form.get("retention_days") or ""))
        except ValueError:
            retention_days = 0
        if not (2 <= len(product_name) <= 50 and 8 <= len(headline) <= 100):
            return await owner_result(
                "Use a 2-50 character product name and 8-100 character headline."
            )
        if not (10 <= len(subhead) <= 240 and 10 <= len(privacy_text) <= 300):
            return await owner_result("Keep launch copy and privacy text concise but complete.")
        if accent not in _ACCENTS or not 7 <= retention_days <= 3650:
            return await owner_result(
                "Choose a theme and retention window between 7 and 3650 days."
            )
        await application.db.execute(
            "UPDATE settings SET product_name = ?, headline = ?, subhead = ?, privacy_text = ?, "
            "accent = ?, retention_days = ? WHERE id = 1",
            product_name,
            headline,
            subhead,
            privacy_text,
            accent,
            retention_days,
        )
        return await owner_result("Brand and privacy settings updated.")

    @application.route("/admin/purge", methods=["POST"], name="admin.purge")
    async def purge_expired() -> MutationResult:
        if not is_admin():
            return await owner_result("Unlock the launch desk before applying retention.")
        current = await settings()
        cutoff = (datetime.now(UTC) - timedelta(days=current.retention_days)).isoformat(
            timespec="seconds"
        )
        await application.db.execute(
            "UPDATE signups SET referred_by = NULL WHERE referred_by IN "
            "(SELECT id FROM signups WHERE created_at < ?)",
            cutoff,
        )
        changed = await application.db.execute("DELETE FROM signups WHERE created_at < ?", cutoff)
        return await owner_result(f"Retention applied; {changed} signup(s) deleted.")

    @application.route("/admin/export.csv", name="admin.export")
    async def export_csv() -> Response:
        if not is_admin():
            return Response("Owner access required", status=403, content_type="text/plain")
        rows = await signups("")
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(
            [
                "created_at",
                "email",
                "name",
                "source",
                "cohort",
                "invite_state",
                "referral_code",
                "direct_referrals",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    _csv_safe(row.created_at),
                    _csv_safe(row.email),
                    _csv_safe(row.name),
                    _csv_safe(row.source),
                    _csv_safe(row.cohort),
                    _csv_safe(row.invite_state),
                    _csv_safe(row.referral_code),
                    row.direct_referrals,
                ]
            )
        return Response(
            output.getvalue(),
            content_type="text/csv; charset=utf-8",
            headers=(
                ("Content-Disposition", 'attachment; filename="chirp-waitlist.csv"'),
                ("Cache-Control", "no-store"),
            ),
        )

    @application.route("/styles.css", referenced=True)
    def styles(request: Request) -> Response:
        return Response(
            (ROOT / "styles.css").read_text(encoding="utf-8"),
            content_type="text/css; charset=utf-8",
        )

    @application.route("/favicon.svg", referenced=True)
    def favicon(request: Request) -> Response:
        return Response(
            (ROOT / "favicon.svg").read_text(encoding="utf-8"),
            content_type="image/svg+xml",
        )

    return application


app = create_app()


if __name__ == "__main__":
    app.run()
