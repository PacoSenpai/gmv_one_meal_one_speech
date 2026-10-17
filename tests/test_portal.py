from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from PIL import Image

from app.extensions import db
from app.models import Comment, Invitation, Reaction, Talk, TalkImage, TalkResource, User, utc_now
from app.services.invitations import hash_invitation_code


def csrf_value(client) -> str:
    cookie = client.get_cookie("csrf_access_token")
    return cookie.value if cookie else ""


def login(client, username="admin", password="a-very-secure-password"):
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": csrf_value(client)},
    )


def test_invitation_registration_is_single_use(client, app, invitation):
    response = client.post(
        "/registro",
        data={
            "invitation_code": invitation,
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
        invitation_row = Invitation.query.filter_by(code_hash=hash_invitation_code(invitation)).one()
        assert invitation_row.used_at is not None

    repeated = client.post(
        "/registro",
        data={
            "csrf_token": csrf_value(client),
            "invitation_code": invitation,
            "username": "second_member",
            "password": "another-secure-password",
        },
    )
    assert repeated.status_code == 400


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
    from re import search

    generated_match = search(rb'id="generated-code">([^<]+)', response.data)
    generated = unescape(generated_match.group(1).decode())
    member_login = login(client, "reset_me", generated)
    assert member_login.status_code == 302
    forced_page = client.get("/")
    assert forced_page.status_code == 302
    assert "/cuenta/contrasena" in forced_page.headers["Location"]


def test_public_pages_api_and_admin_pages_render(client, app, admin_user):
    for path in ("/", "/charlas", "/agenda", "/estadisticas", "/api/health", "/api/talks", "/api/statistics"):
        response = client.get(path)
        assert response.status_code == 200, path
    assert client.get("/api/health").json == {"status": "ok"}

    login(client)
    for path in ("/admin/", "/admin/invitaciones", "/admin/miembros", "/cuenta"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_admin_invitation_is_hashed_and_has_selected_expiry(client, app, admin_user):
    login(client)
    expires_on = datetime.now(ZoneInfo("Europe/Madrid")).date() + timedelta(days=3)
    response = client.post(
        "/admin/invitaciones",
        data={"csrf_token": csrf_value(client), "expires_at": expires_on.isoformat()},
    )
    assert response.status_code == 200
    generated_code = response.data.split(b'id="generated-code">')[1].split(b"<")[0].decode()
    with app.app_context():
        row = Invitation.query.one()
        assert row.code_hash == hash_invitation_code(generated_code)
        assert generated_code not in row.code_hash


def test_member_can_update_profile_and_password(client, app, admin_user):
    login(client)
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
