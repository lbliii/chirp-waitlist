from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest
from chirp.data import DataError, MigrationError, QueryError
from chirp.testing import TestClient

import app as waitlist_app
from app import MAX_BODY, RATE_LIMIT, create_app

pytestmark = pytest.mark.issue(823)
_CSRF_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')


def _application(database: Path):
    return create_app(
        f"sqlite:///{database}",
        admin_token="test-owner-token",
        secret_key="test-signing-key-with-enough-entropy",
    )


def _cookie(response) -> str:
    value = response.header("set-cookie", "")
    assert value.startswith("chirp_session=")
    return value.split(";", 1)[0]


def _updated_cookie(response, fallback: str) -> str:
    value = response.header("set-cookie", "")
    return value.split(";", 1)[0] if value.startswith("chirp_session=") else fallback


async def _page_context(client: TestClient, path: str = "/") -> tuple[str, str]:
    response = await client.get(path)
    match = _CSRF_RE.search(response.text)
    assert match is not None
    return match.group(1), _cookie(response)


async def _join(
    client: TestClient,
    email: str,
    *,
    name: str = "",
    source: str = "direct",
    referral: str = "",
    cookie: str = "",
    htmx: bool = True,
    forwarded_for: str = "203.0.113.10",
):
    if not cookie:
        csrf, cookie = await _page_context(client, f"/?ref={referral}" if referral else "/")
    else:
        page = await client.get("/", headers={"Cookie": cookie})
        match = _CSRF_RE.search(page.text)
        assert match is not None
        csrf = match.group(1)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie,
        "X-Forwarded-For": forwarded_for,
    }
    if htmx:
        headers.update({"HX-Request": "true", "HX-Target": "join-panel"})
    response = await client.post(
        "/join",
        body=urlencode(
            {
                "email": email,
                "name": name,
                "source": source,
                "ref": referral,
                "_csrf_token": csrf,
            }
        ).encode(),
        headers=headers,
    )
    return response, _updated_cookie(response, cookie)


async def _login(client: TestClient) -> tuple[str, str]:
    csrf, cookie = await _page_context(client)
    response = await client.post(
        "/admin/login",
        body=urlencode({"token": "test-owner-token", "_csrf_token": csrf}).encode(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": cookie,
            "HX-Request": "true",
            "HX-Target": "owner-dashboard",
        },
    )
    assert "Launch desk unlocked" in response.text
    owner_cookie = _updated_cookie(response, cookie)
    page = await client.get("/", headers={"Cookie": owner_cookie})
    match = _CSRF_RE.search(page.text)
    assert match is not None
    return match.group(1), owner_cookie


def _admin_headers(cookie: str, *, htmx: bool = True) -> dict[str, str]:
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie}
    if htmx:
        headers.update({"HX-Request": "true", "HX-Target": "owner-dashboard"})
    return headers


async def test_full_page_health_readiness_assets_and_locked_contract(tmp_path: Path) -> None:
    application = _application(tmp_path / "waitlist.db")
    async with TestClient(application) as client:
        page = await client.get("/")
        health = await client.get("/health")
        ready = await client.get("/ready")
        css = await client.get("/styles.css")
        favicon = await client.get("/favicon.svg")

    assert page.status == health.status == ready.status == css.status == favicon.status == 200
    assert favicon.content_type == "image/svg+xml"
    assert '<link rel="icon" href="/favicon.svg" type="image/svg+xml">' in page.text
    assert '<meta name="htmx-config" content=\'{"includeIndicatorStyles": false}\'>' in page.text
    assert page.text.count('data-chirp="htmx"') == 1
    assert "Join us before launch day" in page.text
    assert "Owner-controlled data" in page.text
    assert "Zero-input deploy" in page.text
    assert "No outbound email" in page.text
    csp = page.header("content-security-policy", "")
    assert "'unsafe-inline'" not in csp
    nonce = re.search(r"'nonce-([^']+)'", csp)
    assert nonce is not None and f'nonce="{nonce.group(1)}"' in page.text
    assert "--accent:" in css.text and "--signal:" in css.text


async def test_plain_and_htmx_signup_create_one_place_and_personal_referral(
    tmp_path: Path,
) -> None:
    application = _application(tmp_path / "signup.db")
    async with TestClient(application) as client:
        htmx, cookie = await _join(client, "Ada@Example.COM", name="Ada", source="launch-page")
        personal = await client.get("/", headers={"Cookie": cookie})
        plain, _ = await _join(
            client,
            "grace@example.com",
            name="Grace",
            cookie=(await _page_context(client))[1],
            htmx=False,
            forwarded_for="203.0.113.11",
        )

    assert htmx.status == 200
    assert "You are on the list" in htmx.text
    assert 'id="live-stats"' in htmx.text and "hx-swap-oob" in htmx.text
    assert "Personal referral link" in personal.text
    assert "/?ref=" in personal.text
    assert plain.status == 303 and plain.header("location") == "/?joined=1"
    assert await application.db.fetch_val("SELECT COUNT(*) FROM signups") == 2
    assert (
        await application.db.fetch_val(
            "SELECT email_normalized FROM signups WHERE email = ?", "Ada@Example.COM"
        )
        == "ada@example.com"
    )


async def test_duplicates_are_enumeration_resistant_and_do_not_rewrite_attribution(
    tmp_path: Path,
) -> None:
    application = _application(tmp_path / "duplicate.db")
    async with TestClient(application) as client:
        first, _ = await _join(client, "member@example.com", forwarded_for="203.0.113.20")
        fresh_csrf, fresh_cookie = await _page_context(client)
        duplicate = await client.post(
            "/join",
            body=urlencode(
                {"email": "MEMBER@example.com", "ref": "invalid-ref", "_csrf_token": fresh_csrf}
            ).encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": fresh_cookie,
                "X-Forwarded-For": "203.0.113.21",
                "HX-Request": "true",
                "HX-Target": "join-panel",
            },
        )

    assert "You are on the list" in first.text
    assert "You are on the list" in duplicate.text
    assert "Personal referral link" not in duplicate.text
    assert await application.db.fetch_val("SELECT COUNT(*) FROM signups") == 1
    assert await application.db.fetch_val("SELECT referred_by FROM signups") is None


async def test_driver_without_rowcounts_still_confirms_insert_update_and_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = _application(tmp_path / "rowcounts.db")
    async with TestClient(application) as client:
        original_execute = type(application.db).execute

        async def execute_without_rowcount(database, *args):
            await original_execute(database, *args)
            return 0

        monkeypatch.setattr(type(application.db), "execute", execute_without_rowcount)
        joined, _ = await _join(client, "portable@example.com")
        assert "Personal referral link" in joined.text
        csrf_token, cookie = await _login(client)
        signup_id = await application.db.fetch_val(
            "SELECT id FROM signups WHERE email_normalized = ?", "portable@example.com"
        )
        updated = await client.post(
            f"/admin/signups/{signup_id}",
            body=urlencode(
                {
                    "cohort": "Portable",
                    "invite_state": "invited",
                    "_csrf_token": csrf_token,
                }
            ).encode(),
            headers=_admin_headers(cookie),
        )
        page = await client.get("/", headers={"Cookie": cookie})
        match = _CSRF_RE.search(page.text)
        assert match is not None
        deleted = await client.post(
            f"/admin/signups/{signup_id}/delete",
            body=urlencode({"_csrf_token": match.group(1)}).encode(),
            headers=_admin_headers(cookie),
        )

    assert "Signup updated" in updated.text
    assert "Signup deleted" in deleted.text
    assert await application.db.fetch_val("SELECT COUNT(*) FROM signups") == 0


async def test_referrals_are_direct_bounded_and_self_loop_rewrites_are_ignored(
    tmp_path: Path,
) -> None:
    application = _application(tmp_path / "referrals.db")
    async with TestClient(application) as client:
        await _join(client, "alpha@example.com", forwarded_for="203.0.113.30")
        alpha_id = await application.db.fetch_val(
            "SELECT id FROM signups WHERE email_normalized = ?", "alpha@example.com"
        )
        alpha_ref = await application.db.fetch_val(
            "SELECT referral_code FROM signups WHERE id = ?", alpha_id
        )
        await _join(
            client,
            "beta@example.com",
            referral=alpha_ref,
            forwarded_for="203.0.113.31",
        )
        beta_ref = await application.db.fetch_val(
            "SELECT referral_code FROM signups WHERE email_normalized = ?", "beta@example.com"
        )
        await _join(
            client,
            "alpha@example.com",
            referral=beta_ref,
            forwarded_for="203.0.113.32",
        )

    assert (
        await application.db.fetch_val(
            "SELECT referred_by FROM signups WHERE email_normalized = ?", "beta@example.com"
        )
        == alpha_id
    )
    assert (
        await application.db.fetch_val(
            "SELECT referred_by FROM signups WHERE email_normalized = ?", "alpha@example.com"
        )
        is None
    )
    assert await application.db.fetch_val("SELECT COUNT(*) FROM signups") == 2


async def test_invalid_and_oversized_forms_fail_without_storage(tmp_path: Path) -> None:
    application = _application(tmp_path / "invalid.db")
    async with TestClient(application) as client:
        invalid, cookie = await _join(client, "not-an-email", forwarded_for="203.0.113.40")
        too_long, _ = await _join(
            client,
            "long@example.com",
            name="x" * 81,
            cookie=cookie,
            forwarded_for="203.0.113.40",
        )
        csrf, cookie = await _page_context(client)
        oversized = await client.post(
            "/join",
            body=(
                urlencode({"email": "large@example.com", "_csrf_token": csrf}).encode()
                + b"&padding="
                + b"x" * MAX_BODY
            ),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": cookie,
                "X-Forwarded-For": "203.0.113.41",
            },
        )

    assert invalid.status == too_long.status == 200
    assert "complete email address" in invalid.text
    assert "under 81 characters" in too_long.text
    assert oversized.status == 413
    assert await application.db.fetch_val("SELECT COUNT(*) FROM signups") == 0


async def test_honeypot_is_silent_and_bot_bursts_are_bounded(tmp_path: Path) -> None:
    application = _application(tmp_path / "abuse.db")
    async with TestClient(application) as client:
        csrf, cookie = await _page_context(client)
        honeypot = await client.post(
            "/join",
            body=urlencode(
                {
                    "email": "bot@example.com",
                    "company": "Spambot LLC",
                    "_csrf_token": csrf,
                }
            ).encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": cookie,
                "X-Forwarded-For": "198.51.100.50",
                "HX-Request": "true",
                "HX-Target": "join-panel",
            },
        )
        responses = []
        for index in range(RATE_LIMIT):
            response, _ = await _join(
                client,
                f"person-{index}@example.com",
                forwarded_for="198.51.100.50",
            )
            responses.append(response)

    assert honeypot.status == 200 and "You are on the list" in honeypot.text
    assert [response.status for response in responses].count(200) == RATE_LIMIT - 1
    assert responses[-1].status == 429
    assert (
        await application.db.fetch_val(
            "SELECT COUNT(*) FROM signups WHERE email_normalized = ?", "bot@example.com"
        )
        == 0
    )


async def test_owner_auth_and_locked_mutations_fail_closed(tmp_path: Path) -> None:
    application = _application(tmp_path / "owner.db")
    async with TestClient(application) as client:
        csrf, cookie = await _page_context(client)
        invalid = await client.post(
            "/admin/login",
            body=urlencode({"token": "wrong", "_csrf_token": csrf}).encode(),
            headers=_admin_headers(cookie),
        )
        match = _CSRF_RE.search(invalid.text)
        assert match is not None
        locked = await client.post(
            "/admin/settings",
            body=urlencode(
                {
                    "product_name": "Changed",
                    "headline": "A changed launch headline",
                    "subhead": "This is enough copy to pass validation safely.",
                    "privacy_text": "This is enough privacy text to pass validation.",
                    "accent": "blue",
                    "retention_days": "30",
                    "_csrf_token": match.group(1),
                }
            ).encode(),
            headers=_admin_headers(cookie),
        )

    assert "owner token was not accepted" in invalid.text
    assert "Unlock the launch desk" in locked.text
    assert await application.db.fetch_val("SELECT product_name FROM settings") == "Northstar"


async def test_owner_search_cohort_invite_and_htmx_aggregate_updates(tmp_path: Path) -> None:
    application = _application(tmp_path / "manage.db")
    async with TestClient(application) as client:
        await _join(client, "search@example.com", name="Search Person")
        csrf, cookie = await _login(client)
        signup_id = await application.db.fetch_val(
            "SELECT id FROM signups WHERE email_normalized = ?", "search@example.com"
        )
        updated = await client.post(
            f"/admin/signups/{signup_id}",
            body=urlencode(
                {
                    "cohort": "Founding 25",
                    "invite_state": "invited",
                    "_csrf_token": csrf,
                }
            ).encode(),
            headers=_admin_headers(cookie),
        )
        fragment = await client.get(
            "/?q=Founding",
            headers={"Cookie": cookie, "HX-Request": "true", "HX-Target": "owner-dashboard"},
        )
        live = await client.get("/stats", headers={"HX-Request": "true"})

    assert "Signup updated" in updated.text
    assert 'id="live-stats"' in updated.text and "hx-swap-oob" in updated.text
    assert "Founding 25" in fragment.text and "search@example.com" in fragment.text
    assert "<!doctype html>" not in fragment.text.lower()
    assert "Invites opened" in live.text and ">1<" in live.text


async def test_brand_privacy_settings_and_retention_purge(tmp_path: Path) -> None:
    application = _application(tmp_path / "settings.db")
    async with TestClient(application) as client:
        await _join(client, "old@example.com")
        old = (datetime.now(UTC) - timedelta(days=40)).isoformat(timespec="seconds")
        await application.db.execute(
            "UPDATE signups SET created_at = ? WHERE email_normalized = ?", old, "old@example.com"
        )
        csrf, cookie = await _login(client)
        changed = await client.post(
            "/admin/settings",
            body=urlencode(
                {
                    "product_name": "Orbit Notes",
                    "headline": "A quieter launch is coming.",
                    "subhead": "Join the small group shaping our first useful release.",
                    "privacy_text": (
                        "We keep this signup for the launch only and honor deletion requests."
                    ),
                    "accent": "blue",
                    "retention_days": "30",
                    "_csrf_token": csrf,
                }
            ).encode(),
            headers=_admin_headers(cookie),
        )
        page = await client.get("/", headers={"Cookie": cookie})
        match = _CSRF_RE.search(page.text)
        assert match is not None
        purged = await client.post(
            "/admin/purge",
            body=urlencode({"_csrf_token": match.group(1)}).encode(),
            headers=_admin_headers(cookie),
        )

    assert "Brand and privacy settings updated" in changed.text
    assert "Orbit Notes" in page.text and 'class="theme-blue"' in page.text
    assert "1 signup(s) deleted" in purged.text
    assert await application.db.fetch_val("SELECT COUNT(*) FROM signups") == 0


async def test_xss_is_escaped_and_csv_formula_cells_are_neutralized(tmp_path: Path) -> None:
    application = _application(tmp_path / "output.db")
    async with TestClient(application) as client:
        await _join(
            client,
            "safe@example.com",
            name="<script>alert(1)</script>",
            source="=IMPORTXML(A1)",
        )
        _, cookie = await _login(client)
        page = await client.get("/", headers={"Cookie": cookie})
        exported = await client.get("/admin/export.csv", headers={"Cookie": cookie})

    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    assert "'=IMPORTXML(A1)" in exported.text
    assert exported.header("content-disposition") == 'attachment; filename="chirp-waitlist.csv"'
    assert exported.header("cache-control") == "no-store"


async def test_delete_clears_referrer_and_plain_owner_update_redirects(tmp_path: Path) -> None:
    application = _application(tmp_path / "delete.db")
    async with TestClient(application) as client:
        await _join(client, "referrer@example.com", forwarded_for="203.0.113.70")
        referrer_id = await application.db.fetch_val(
            "SELECT id FROM signups WHERE email_normalized = ?", "referrer@example.com"
        )
        referral = await application.db.fetch_val(
            "SELECT referral_code FROM signups WHERE id = ?", referrer_id
        )
        await _join(
            client,
            "referred@example.com",
            referral=referral,
            forwarded_for="203.0.113.71",
        )
        csrf, cookie = await _login(client)
        plain = await client.post(
            f"/admin/signups/{referrer_id}",
            body=urlencode(
                {"cohort": "Alpha", "invite_state": "joined", "_csrf_token": csrf}
            ).encode(),
            headers=_admin_headers(cookie, htmx=False),
        )
        page = await client.get("/", headers={"Cookie": cookie})
        match = _CSRF_RE.search(page.text)
        assert match is not None
        deleted = await client.post(
            f"/admin/signups/{referrer_id}/delete",
            body=urlencode({"_csrf_token": match.group(1)}).encode(),
            headers=_admin_headers(cookie),
        )

    assert plain.status == 303 and plain.header("location") == "/#owner"
    assert "Signup deleted" in deleted.text
    assert await application.db.fetch_val("SELECT COUNT(*) FROM signups") == 1
    assert await application.db.fetch_val("SELECT referred_by FROM signups") is None


async def test_restart_preserves_signup_brand_and_owner_state(tmp_path: Path) -> None:
    database = tmp_path / "persistent.db"
    first = _application(database)
    async with TestClient(first) as client:
        await _join(client, "persistent@example.com", name="Persistent Person")
        csrf, cookie = await _login(client)
        signup_id = await first.db.fetch_val(
            "SELECT id FROM signups WHERE email_normalized = ?", "persistent@example.com"
        )
        await client.post(
            f"/admin/signups/{signup_id}",
            body=urlencode(
                {"cohort": "Keepers", "invite_state": "invited", "_csrf_token": csrf}
            ).encode(),
            headers=_admin_headers(cookie),
        )

    second = _application(database)
    async with TestClient(second) as client:
        _, cookie = await _login(client)
        page = await client.get("/?q=Keepers", headers={"Cookie": cookie})

    assert "persistent@example.com" in page.text
    assert "Keepers" in page.text
    assert await second.db.fetch_val("SELECT invite_state FROM signups") == "invited"


async def test_database_and_migration_failures_are_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unavailable = create_app(
        "postgresql://postgres:postgres@127.0.0.1:1/railway",
        admin_token="test-owner-token",
        secret_key="test-signing-key-with-enough-entropy",
    )
    with pytest.raises(DataError, match=r"could not connect to 127\.0\.0\.1:1"):
        async with TestClient(unavailable):
            pass

    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_broken.sql").write_text("CREATE TABL broken (", encoding="utf-8")
    monkeypatch.setattr(waitlist_app, "MIGRATIONS", migrations)
    broken = _application(tmp_path / "broken.db")
    with pytest.raises(MigrationError, match="Migration 001_broken failed"):
        async with TestClient(broken):
            pass


async def test_schema_mismatch_fails_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_wrong.sql").write_text(
        "CREATE TABLE settings (id INTEGER PRIMARY KEY);", encoding="utf-8"
    )
    monkeypatch.setattr(waitlist_app, "MIGRATIONS", migrations)
    application = _application(tmp_path / "wrong.db")
    with pytest.raises(QueryError, match="product_name"):
        async with TestClient(application):
            pass


def test_app_contracts_pass(tmp_path: Path) -> None:
    application = _application(tmp_path / "contracts.db")
    assert application.config.workers == 1
    assert application.config.max_request_body_size == MAX_BODY
    application.freeze()
    assert any(check.name == "database" for check in application._mutable_state.health_checks)
    application.check()
