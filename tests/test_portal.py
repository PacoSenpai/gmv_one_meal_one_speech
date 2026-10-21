from __future__ import annotations

import re
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from flask_jwt_extended import create_access_token, decode_token
from PIL import Image

from app import create_app
from app.extensions import db
from app.models import Comment, Reaction, Talk, TalkImage, TalkResource, User, utc_now


def csrf_value(client) -> str:
    cookie = client.get_cookie("csrf_access_token")
    return cookie.value if cookie else ""


def form_csrf_value(client, path: str) -> str:
    response = client.get(path)
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    assert match, f"CSRF field not rendered on {path}"
    return match.group(1).decode()


def login(client, username="admin", password="a-very-secure-password", next_url="", remember=None):
    csrf_token = form_csrf_value(client, "/login")
    data = {
        "username": username,
        "password": password,
        "csrf_token": csrf_token,
        "next": next_url,
    }
    if remember is not None:
        data["remember"] = "1" if remember else "0"
    return client.post("/login", data=data)


def test_public_registration_creates_active_member_without_invitation(client, app):
    form = client.get("/registro")
    assert form.status_code == 200
    assert b"invitaci" not in form.data.lower()

    csrf_token = form_csrf_value(client, "/registro")
    response = client.post(
        "/registro",
        data={
            "csrf_token": csrf_token,
            "username": "new_member",
            "full_name": "New Member",
            "password": "a-different-secure-password",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        created = User.query.filter_by(username="new_member").one()
        assert created.role == "member"
        assert created.full_name == "New Member"
        assert created.check_password("a-different-secure-password")
        assert not created.must_change_password
        assert created.is_active
        assert not created.is_superadmin


def test_public_proposal_is_scheduled_and_resources_are_saved(client, app, admin_user):
    login(client)
    response = client.post(
        "/proponer",
        data={
            "csrf_token": csrf_value(client),
            "title": "Ideas from the edge of space",
            "description": "A sufficiently detailed description about the subject and why it is interesting.",
            "resources": "Mission archive | https://example.org/archive",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        talk = Talk.query.one()
        assert talk.status == "scheduled"
        assert talk.event_at > utc_now()
        assert talk.speaker_id == admin_user
        assert talk.resources[0].label == "Mission archive"
        assert talk.resources[0].url == "https://example.org/archive"


def test_proposal_image_is_reencoded(client, app, admin_user):
    login(client)
    image_buffer = BytesIO()
    Image.new("RGB", (32, 24), color=(226, 0, 26)).save(image_buffer, format="PNG")
    image_buffer.seek(0)
    response = client.post(
        "/proponer",
        data={
            "csrf_token": csrf_value(client),
            "title": "A visual talk about careful image handling",
            "description": "A sufficiently long description about the subject and the image upload test.",
            "images": (image_buffer, "image.png"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    with app.app_context():
        talk = Talk.query.one()
        assert len(talk.images) == 1
        assert talk.images[0].filename.endswith(".webp")


def test_only_talk_speaker_can_reply_and_one_vote_per_member(client, app, admin_user):
    with app.app_context():
        admin = db.session.get(User, admin_user)
        member = User(username="listener")
        member.set_password("listener-secure-password")
        talk = Talk(
            title="A completed talk",
            description="A sufficiently detailed description about a completed presentation.",
            event_at=utc_now() - timedelta(days=2),
            completed_at=utc_now() - timedelta(hours=1),
            status="completed",
            speaker=admin,
        )
        db.session.add_all([member, talk])
        db.session.commit()
        talk_id, member_id = talk.id, member.id

    login(client, "listener", "listener-secure-password")
    reaction_response = client.post(
        f"/charlas/{talk_id}/valoracion",
        data={"csrf_token": csrf_value(client), "value": "like"},
    )
    assert reaction_response.status_code == 302
    second_reaction_response = client.post(
        f"/charlas/{talk_id}/valoracion",
        data={"csrf_token": csrf_value(client), "value": "dislike"},
    )
    assert second_reaction_response.status_code == 302
    comment_response = client.post(
        f"/charlas/{talk_id}/comentarios",
        data={"csrf_token": csrf_value(client), "body": "Me gustó mucho el enfoque."},
    )
    assert comment_response.status_code == 302
    with app.app_context():
        assert Reaction.query.filter_by(talk_id=talk_id, user_id=member_id).count() == 1
        assert Reaction.query.filter_by(talk_id=talk_id, user_id=member_id).one().value == "dislike"
        comment = Comment.query.filter_by(talk_id=talk_id).one()
        comment_id = comment.id

    forbidden_reply = client.post(
        f"/charlas/{talk_id}/comentarios/{comment_id}/responder",
        data={"csrf_token": csrf_value(client), "body": "No soy el ponente."},
    )
    assert forbidden_reply.status_code == 403


def test_temporary_password_requires_a_change(client, app, admin_user):
    with app.app_context():
        member = User(username="reset_me")
        member.set_password("previous-secure-password")
        db.session.add(member)
        db.session.commit()
        member_id = member.id

    login(client)
    response = client.post(
        f"/admin/miembros/{member_id}/restablecer-contrasena",
        data={"csrf_token": csrf_value(client)},
    )
    assert response.status_code == 200
    with app.app_context():
        member = db.session.get(User, member_id)
        assert member.must_change_password
        assert member.password_hash.startswith("scrypt:")

    client.post("/logout", data={"csrf_token": csrf_value(client)})
    login_response = login(client, "reset_me", "invalid-password")
    assert login_response.status_code == 401
    # The generated password is intentionally shown once in the admin response, not stored in plaintext.
    from html import unescape

    generated_match = re.search(rb'id="generated-code">([^<]+)', response.data)
    generated = unescape(generated_match.group(1).decode())
    member_login = login(client, "reset_me", generated)
    assert member_login.status_code == 302
    forced_register = client.get("/registro")
    assert forced_register.status_code == 302
    assert "/cuenta/contrasena" in forced_register.headers["Location"]
    forced_page = client.get("/")
    assert forced_page.status_code == 302
    assert "/cuenta/contrasena" in forced_page.headers["Location"]


def test_public_pages_api_and_admin_pages_render(client, app, admin_user):
    for path in ("/", "/charlas", "/agenda", "/estadisticas", "/api/health", "/api/talks", "/api/statistics"):
        response = client.get(path)
        assert response.status_code == 200, path
    assert client.get("/api/health").json == {"status": "ok"}

    login(client)
    for path in ("/admin/", "/admin/miembros", "/cuenta"):
        response = client.get(path)
        assert response.status_code == 200, path
    assert client.get("/admin/invitaciones").status_code == 404


def test_member_can_update_profile_and_password(client, app, admin_user):
    login(client, remember=True)
    profile_response = client.post(
        "/cuenta",
        data={
            "csrf_token": csrf_value(client),
            "username": "updated_admin",
            "full_name": "Updated Name",
        },
    )
    assert profile_response.status_code == 302
    password_response = client.post(
        "/cuenta/contrasena",
        data={
            "csrf_token": csrf_value(client),
            "current_password": "a-very-secure-password",
            "new_password": "a-new-and-more-secure-password",
        },
    )
    assert password_response.status_code == 302
    renewed_cookie = client.get_cookie("access_token_cookie")
    assert renewed_cookie is not None
    with app.app_context():
        renewed_claims = decode_token(renewed_cookie.value)
    assert renewed_claims["remember"] is True
    assert renewed_claims["exp"] - renewed_claims["iat"] == int(
        timedelta(days=app.config["SESSION_REMEMBER_DAYS"]).total_seconds()
    )
    with app.app_context():
        user = db.session.get(User, admin_user)
        assert user.username == "updated_admin"
        assert user.full_name == "Updated Name"
        assert user.check_password("a-new-and-more-secure-password")


def test_archive_lists_upcoming_and_completed_talks(client, app, admin_user):
    with app.app_context():
        speaker = db.session.get(User, admin_user)
        completed_at = utc_now() - timedelta(hours=1)
        db.session.add_all(
            [
                Talk(
                    title="An archived conversation",
                    description="A sufficiently long description for an archived conversation.",
                    event_at=completed_at - timedelta(hours=1),
                    completed_at=completed_at,
                    status="completed",
                    speaker=speaker,
                ),
                Talk(
                    title="A future conversation",
                    description="A sufficiently long description for a future conversation.",
                    event_at=utc_now() + timedelta(days=10),
                    status="scheduled",
                    speaker=speaker,
                ),
            ]
        )
        db.session.commit()

    upcoming_response = client.get("/charlas")
    completed_response = client.get("/charlas?tipo=realizadas")
    assert upcoming_response.status_code == 200
    assert b"A future conversation" in upcoming_response.data
    assert completed_response.status_code == 200
    assert b"An archived conversation" in completed_response.data


def test_owner_can_edit_and_delete_only_their_future_talk(client, app, admin_user, tmp_path):
    image_path = tmp_path / "uploads" / "owned-image.webp"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"test image placeholder")
    with app.app_context():
        owner = db.session.get(User, admin_user)
        other_member = User(username="another_speaker")
        other_member.set_password("another-secure-password")
        db.session.add(other_member)
        db.session.flush()
        own_event_at = utc_now() + timedelta(days=10)
        other_event_at = utc_now() + timedelta(days=20)
        third_event_at = utc_now() + timedelta(days=30)
        own_talk = Talk(
            title="My original scheduled talk",
            description="A sufficiently long description for my scheduled talk.",
            event_at=own_event_at,
            status="scheduled",
            speaker=owner,
            resources=[TalkResource(label="Old resource", url="https://example.org/old")],
            images=[TalkImage(filename="owned-image.webp", position=0)],
        )
        other_talk = Talk(
            title="Another member's scheduled talk",
            description="A sufficiently long description for another scheduled talk.",
            event_at=other_event_at,
            status="scheduled",
            speaker=other_member,
        )
        third_talk = Talk(
            title="A third scheduled talk",
            description="A sufficiently long description for a third scheduled talk.",
            event_at=third_event_at,
            status="scheduled",
            speaker=other_member,
        )
        db.session.add_all([own_talk, other_talk, third_talk])
        db.session.commit()
        own_talk_id, other_talk_id, third_talk_id = own_talk.id, other_talk.id, third_talk.id
        own_image_id = own_talk.images[0].id

    login(client)
    assert client.get("/mis-charlas").status_code == 200
    edit_response = client.post(
        f"/charlas/{own_talk_id}/editar",
        data={
            "csrf_token": csrf_value(client),
            "title": "My corrected scheduled talk",
            "description": "A sufficiently long corrected description for my scheduled talk.",
            "resources": "New resource | https://example.org/new",
            "remove_images": str(own_image_id),
        },
    )
    assert edit_response.status_code == 302
    with app.app_context():
        talk = db.session.get(Talk, own_talk_id)
        assert talk.title == "My corrected scheduled talk"
        assert talk.resources[0].label == "New resource"
        assert not talk.images
        assert not image_path.exists()

    forbidden_edit = client.post(
        f"/charlas/{other_talk_id}/editar",
        data={"csrf_token": csrf_value(client)},
    )
    assert forbidden_edit.status_code == 403
    forbidden_delete = client.post(
        f"/charlas/{other_talk_id}/eliminar",
        data={"csrf_token": csrf_value(client)},
    )
    assert forbidden_delete.status_code == 403

    delete_response = client.post(
        f"/charlas/{own_talk_id}/eliminar",
        data={"csrf_token": csrf_value(client)},
    )
    assert delete_response.status_code == 302
    with app.app_context():
        assert db.session.get(Talk, own_talk_id) is None
        assert db.session.get(Talk, other_talk_id).event_at == own_event_at
        assert db.session.get(Talk, third_talk_id).event_at == other_event_at


def test_owner_defers_one_position_by_swapping_dates(client, app, admin_user):
    with app.app_context():
        owner = db.session.get(User, admin_user)
        second_speaker = User(username="second_speaker")
        second_speaker.set_password("second-speaker-secure-password")
        third_speaker = User(username="third_speaker")
        third_speaker.set_password("third-speaker-secure-password")
        db.session.add_all([second_speaker, third_speaker])
        db.session.flush()
        first_date = utc_now() + timedelta(days=12)
        second_date = utc_now() + timedelta(days=42)
        third_date = utc_now() + timedelta(days=72)
        first_talk = Talk(
            title="My scheduled talk",
            description="A sufficiently long description for the first scheduled talk.",
            event_at=first_date,
            status="scheduled",
            speaker=owner,
        )
        second_talk = Talk(
            title="The next speaker's talk",
            description="A sufficiently long description for the next scheduled talk.",
            event_at=second_date,
            status="scheduled",
            speaker=second_speaker,
        )
        third_talk = Talk(
            title="The third speaker's talk",
            description="A sufficiently long description for the third scheduled talk.",
            event_at=third_date,
            status="scheduled",
            speaker=third_speaker,
        )
        db.session.add_all([first_talk, second_talk, third_talk])
        db.session.commit()
        first_talk_id, second_talk_id, third_talk_id = first_talk.id, second_talk.id, third_talk.id

    login(client)
    response = client.post(
        f"/charlas/{first_talk_id}/ceder-turno",
        data={"csrf_token": csrf_value(client)},
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Talk, first_talk_id).event_at == second_date
        assert db.session.get(Talk, second_talk_id).event_at == first_date
        assert db.session.get(Talk, third_talk_id).event_at == third_date


def test_last_talk_cannot_defer_without_a_next_talk(client, app, admin_user):
    with app.app_context():
        speaker = db.session.get(User, admin_user)
        talk = Talk(
            title="Last scheduled talk",
            description="A sufficiently long description for the last scheduled talk.",
            event_at=utc_now() + timedelta(days=10),
            status="scheduled",
            speaker=speaker,
        )
        db.session.add(talk)
        db.session.commit()
        talk_id = talk.id

    login(client)
    response = client.post(
        f"/charlas/{talk_id}/ceder-turno",
        data={"csrf_token": csrf_value(client)},
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Talk, talk_id).event_at > utc_now()


def test_owner_cannot_edit_or_delete_completed_talk(client, app, admin_user):
    with app.app_context():
        speaker = db.session.get(User, admin_user)
        completed_at = utc_now() - timedelta(hours=2)
        talk = Talk(
            title="Already completed talk",
            description="A sufficiently long description for an already completed talk.",
            event_at=completed_at - timedelta(hours=1),
            completed_at=completed_at,
            status="completed",
            speaker=speaker,
        )
        db.session.add(talk)
        db.session.commit()
        talk_id = talk.id

    login(client)
    edit_response = client.post(
        f"/charlas/{talk_id}/editar",
        data={"csrf_token": csrf_value(client)},
    )
    delete_response = client.post(
        f"/charlas/{talk_id}/eliminar",
        data={"csrf_token": csrf_value(client)},
    )
    assert edit_response.status_code == 302
    assert delete_response.status_code == 400
    with app.app_context():
        assert db.session.get(Talk, talk_id).status == "completed"


def test_password_change_cannot_be_forced_by_query_parameter(client, app):
    with app.app_context():
        user = User(username="reauth_user")
        user.set_password("the-original-secure-password")
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    login(client, "reauth_user", "the-original-secure-password")
    response = client.post(
        "/cuenta/contrasena?forced=1",
        data={
            "csrf_token": csrf_value(client),
            "new_password": "an-attacker-chosen-new-password",
        },
    )
    assert response.status_code == 400
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.check_password("the-original-secure-password")
        assert not user.check_password("an-attacker-chosen-new-password")


def test_anonymous_login_and_registration_require_preauth_csrf(client, app, admin_user):
    login_without_token = client.post(
        "/login",
        data={"username": "admin", "password": "a-very-secure-password"},
    )
    assert login_without_token.status_code == 400

    register_without_token = client.post(
        "/registro",
        data={
            "username": "csrf_missing",
            "password": "a-different-secure-password",
        },
    )
    assert register_without_token.status_code == 400
    with app.app_context():
        assert User.query.filter_by(username="csrf_missing").first() is None


def test_authenticated_cookie_does_not_skip_login_csrf(client, admin_user):
    login(client)
    response = client.post(
        "/login",
        data={"username": "admin", "password": "a-very-secure-password"},
    )
    assert response.status_code == 400


def test_login_redirect_rejects_backslash_and_external_targets(client, admin_user):
    for target in ("/\\attacker.example", "//attacker.example", "https://attacker.example"):
        response = login(client, next_url=target)
        assert response.status_code == 302
        assert response.headers["Location"] == "/"


def test_login_form_checks_remember_session_by_default(client):
    response = client.get("/login")
    assert b'name="remember" value="1" checked' in response.data


def _session_lifetime(client, app, response):
    cookie = client.get_cookie("access_token_cookie")
    assert cookie is not None
    with app.app_context():
        claims = decode_token(cookie.value)
    set_cookie = next(
        header
        for header in response.headers.getlist("Set-Cookie")
        if header.startswith("access_token_cookie=")
    )
    if not isinstance(set_cookie, str):
        set_cookie = "; ".join(set_cookie)
    return claims, set_cookie, claims["exp"] - claims["iat"]


def test_remember_me_extends_session_to_configured_days(client, app, admin_user):
    response = login(client, remember=True)
    assert response.status_code == 302
    claims, set_cookie, lifetime = _session_lifetime(client, app, response)
    expected = int(timedelta(days=app.config["SESSION_REMEMBER_DAYS"]).total_seconds())
    assert claims["remember"] is True
    assert lifetime == expected
    assert f"Max-Age={expected}" in set_cookie


def test_login_without_remember_keeps_eight_hour_session(client, app, admin_user):
    response = login(client, remember=False)
    assert response.status_code == 302
    claims, set_cookie, lifetime = _session_lifetime(client, app, response)
    assert claims["remember"] is False
    expected = int(timedelta(hours=8).total_seconds())
    assert lifetime == expected
    assert f"Max-Age={expected}" in set_cookie


def test_registration_can_start_a_long_session(client, app):
    csrf_token = form_csrf_value(client, "/registro")
    response = client.post(
        "/registro",
        data={
            "csrf_token": csrf_token,
            "username": "long_session_member",
            "full_name": "Long Session Member",
            "password": "a-different-secure-password",
            "remember": "1",
        },
    )
    assert response.status_code == 302
    claims, _set_cookie, lifetime = _session_lifetime(client, app, response)
    assert claims["remember"] is True
    assert lifetime == int(
        timedelta(days=app.config["SESSION_REMEMBER_DAYS"]).total_seconds()
    )


def test_expired_session_redirects_to_login_instead_of_json(client, app, admin_user):
    with app.app_context():
        token = create_access_token(
            identity=str(admin_user),
            additional_claims={"auth_version": 0, "remember": True},
            expires_delta=timedelta(seconds=-1),
        )
    client.set_cookie("access_token_cookie", token)
    response = client.get("/cuenta")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert b"Token has expired" not in response.data
    followed = client.get(response.headers["Location"])
    assert "Tu sesión ha caducado".encode() in followed.data


def test_revoked_session_redirects_to_login_instead_of_json(client, app, admin_user):
    login(client, remember=True)
    with app.app_context():
        user = db.session.get(User, admin_user)
        user.auth_version += 1
        db.session.commit()
    response = client.get("/cuenta")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert b"Token has been revoked" not in response.data
    followed = client.get(response.headers["Location"])
    assert "Tu sesión ya no es válida".encode() in followed.data


def test_oversized_login_fields_are_rejected_before_password_hash_check(client, app, admin_user):
    csrf_token = form_csrf_value(client, "/login")
    with patch.object(User, "check_password", return_value=False) as check_password:
        response = client.post(
            "/login",
            data={
                "csrf_token": csrf_token,
                "username": "admin",
                "password": "x" * 257,
            },
        )
    assert response.status_code == 401
    check_password.assert_not_called()


def test_login_rate_limit_returns_429(client):
    csrf_token = form_csrf_value(client, "/login")
    responses = [
        client.post(
            "/login",
            data={
                "csrf_token": csrf_token,
                "username": "unknown_user",
                "password": "invalid-password",
            },
        )
        for _ in range(6)
    ]
    assert [response.status_code for response in responses[:5]] == [401] * 5
    assert responses[5].status_code == 429


def test_archive_page_input_is_bounded(client):
    assert client.get("/charlas?page=999999999999999999999999").status_code == 400


def test_production_requires_distinct_secrets_secure_cookies_and_redis(tmp_path):
    base_config = {
        "TESTING": False,
        "APP_ENV": "production",
        "SECRET_KEY": "a-secret-key-with-at-least-32-bytes",
        "JWT_SECRET_KEY": "a-different-jwt-key-with-at-least-32-bytes",
        "DATABASE_PATH": str(tmp_path / "production.sqlite3"),
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "JWT_COOKIE_SECURE": True,
        "RATELIMIT_STORAGE_URI": "memory://",
    }
    with pytest.raises(RuntimeError, match="Redis compartido"):
        create_app(base_config)
    with pytest.raises(RuntimeError, match="Redis compartido"):
        create_app({**base_config, "TESTING": True})

    insecure_config = {
        **base_config,
        "RATELIMIT_STORAGE_URI": "redis://127.0.0.1:6379/0",
        "JWT_COOKIE_SECURE": False,
    }
    with pytest.raises(RuntimeError, match="COOKIE_SECURE=1"):
        create_app(insecure_config)

    same_secret_config = {
        **base_config,
        "DATABASE_PATH": None,
        "APP_ENV": "development",
        "JWT_SECRET_KEY": base_config["SECRET_KEY"],
    }
    with pytest.raises(RuntimeError, match="deben ser valores distintos"):
        create_app(same_secret_config)


def test_first_cli_admin_becomes_superadmin(app):
    runner = app.test_cli_runner()
    with patch(
        "app.cli.getpass.getpass",
        side_effect=["first-admin-secure-password", "first-admin-secure-password"],
    ):
        first_result = runner.invoke(args=["create-admin"], input="first_admin\n\n")
    assert first_result.exit_code == 0, first_result.output

    with patch(
        "app.cli.getpass.getpass",
        side_effect=["second-admin-secure-password", "second-admin-secure-password"],
    ):
        second_result = runner.invoke(args=["create-admin"], input="second_admin\n\n")
    assert second_result.exit_code == 0, second_result.output
    with app.app_context():
        first_admin = User.query.filter_by(username="first_admin").one()
        second_admin = User.query.filter_by(username="second_admin").one()
        assert first_admin.is_superadmin
        assert not second_admin.is_superadmin


def test_schedule_changes_reorganize_all_future_scheduled_talks(client, app, admin_user):
    with app.app_context():
        speaker = db.session.get(User, admin_user)
        now = utc_now()
        first_talk = Talk(
            title="Earlier queued speech",
            description="A sufficiently detailed description for the first queued speech.",
            event_at=now + timedelta(days=8),
            status="scheduled",
            speaker=speaker,
        )
        second_talk = Talk(
            title="Later queued speech",
            description="A sufficiently detailed description for the second queued speech.",
            event_at=now + timedelta(days=200),
            status="scheduled",
            speaker=speaker,
        )
        completed_talk = Talk(
            title="Completed speech",
            description="A sufficiently detailed description for a completed speech.",
            event_at=now + timedelta(days=30),
            completed_at=now,
            status="completed",
            speaker=speaker,
        )
        cancelled_talk = Talk(
            title="Cancelled speech",
            description="A sufficiently detailed description for a cancelled speech.",
            event_at=now + timedelta(days=40),
            status="cancelled",
            speaker=speaker,
        )
        past_scheduled_talk = Talk(
            title="Past scheduled speech",
            description="A sufficiently detailed description for a past scheduled speech.",
            event_at=now - timedelta(days=2),
            status="scheduled",
            speaker=speaker,
        )
        db.session.add_all([first_talk, second_talk, completed_talk, cancelled_talk, past_scheduled_talk])
        db.session.commit()
        first_id, second_id = first_talk.id, second_talk.id
        completed_id, cancelled_id, past_scheduled_id = (
            completed_talk.id,
            cancelled_talk.id,
            past_scheduled_talk.id,
        )
        completed_at = completed_talk.event_at
        cancelled_at = cancelled_talk.event_at
        past_at = past_scheduled_talk.event_at

    login(client)
    response = client.post(
        "/admin/agenda",
        data={
            "csrf_token": csrf_value(client),
            "default_time": "14:00",
            "slot": "2:1",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        first_talk = db.session.get(Talk, first_id)
        second_talk = db.session.get(Talk, second_id)
        first_local = first_talk.event_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(
            ZoneInfo("Europe/Madrid")
        )
        second_local = second_talk.event_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(
            ZoneInfo("Europe/Madrid")
        )
        assert first_local.weekday() == second_local.weekday() == 2
        assert first_local.hour == second_local.hour == 14
        assert first_local.minute == second_local.minute == 0
        assert first_talk.event_at < second_talk.event_at
        assert db.session.get(Talk, completed_id).event_at == completed_at
        assert db.session.get(Talk, cancelled_id).event_at == cancelled_at
        assert db.session.get(Talk, past_scheduled_id).event_at == past_at


def test_only_superadmin_can_manage_other_admins_and_reset_passwords(client, app, admin_user):
    with app.app_context():
        regular_admin = User(username="regular_admin", role="admin", is_superadmin=False)
        other_admin = User(username="other_admin", role="admin", is_superadmin=False)
        another_admin = User(username="another_admin", role="admin", is_superadmin=False)
        member = User(username="managed_member", role="member")
        for user, password in (
            (regular_admin, "regular-admin-secure-password"),
            (other_admin, "other-admin-secure-password"),
            (another_admin, "another-admin-secure-password"),
            (member, "managed-member-secure-password"),
        ):
            user.set_password(password)
        db.session.add_all([regular_admin, other_admin, another_admin, member])
        db.session.commit()
        regular_admin_id = regular_admin.id
        other_admin_id = other_admin.id
        another_admin_id = another_admin.id
        member_id = member.id

    login(client, "regular_admin", "regular-admin-secure-password")
    demote_response = client.post(
        f"/admin/miembros/{other_admin_id}/rol",
        data={"csrf_token": csrf_value(client), "role": "member"},
    )
    disable_response = client.post(
        f"/admin/miembros/{other_admin_id}/estado",
        data={"csrf_token": csrf_value(client), "active": "0"},
    )
    reset_response = client.post(
        f"/admin/miembros/{member_id}/restablecer-contrasena",
        data={"csrf_token": csrf_value(client)},
    )
    self_demote_response = client.post(
        f"/admin/miembros/{regular_admin_id}/rol",
        data={"csrf_token": csrf_value(client), "role": "member"},
    )
    protect_superadmin_response = client.post(
        f"/admin/miembros/{admin_user}/rol",
        data={"csrf_token": csrf_value(client), "role": "member"},
    )
    assert demote_response.status_code == 403
    assert disable_response.status_code == 403
    assert reset_response.status_code == 403
    assert self_demote_response.status_code == 403
    assert protect_superadmin_response.status_code == 403

    promote_response = client.post(
        f"/admin/miembros/{member_id}/rol",
        data={"csrf_token": csrf_value(client), "role": "admin"},
    )
    assert promote_response.status_code == 302
    client.post("/logout", data={"csrf_token": csrf_value(client)})

    login(client)
    superadmin_demote = client.post(
        f"/admin/miembros/{other_admin_id}/rol",
        data={"csrf_token": csrf_value(client), "role": "member"},
    )
    superadmin_disable = client.post(
        f"/admin/miembros/{another_admin_id}/estado",
        data={"csrf_token": csrf_value(client), "active": "0"},
    )
    superadmin_reset = client.post(
        f"/admin/miembros/{member_id}/restablecer-contrasena",
        data={"csrf_token": csrf_value(client)},
    )
    assert superadmin_demote.status_code == 302
    assert superadmin_disable.status_code == 302
    assert superadmin_reset.status_code == 200

    with app.app_context():
        assert db.session.get(User, admin_user).is_superadmin
        assert db.session.get(User, admin_user).role == "admin"
        assert db.session.get(User, regular_admin_id).role == "admin"
        assert db.session.get(User, other_admin_id).role == "member"
        assert not db.session.get(User, another_admin_id).is_active
        promoted_member = db.session.get(User, member_id)
        assert promoted_member.role == "admin"
        assert promoted_member.must_change_password
