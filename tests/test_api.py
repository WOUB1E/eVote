from datetime import datetime, timezone
from io import BytesIO

from app import uploaded_filename, uploaded_filename_list
from models import Option, Poll, PollAuditLog, PollComment, PollView, User, db

PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


def login_api(client, username="alice", password="password123"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.get_json()


def auth_headers(payload):
    return {
        "Authorization": f"Bearer {payload['token']}",
        "X-CSRF-Token": payload["csrf_token"],
    }


def test_cloudinary_storage_refs_survive_filename_cleanup():
    value = "cloudinary:evote/poll_abc-123"

    assert uploaded_filename(value) == value
    assert uploaded_filename_list([value]) == [value]


def test_root_serves_react_app(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Система электронного голосования" in response.get_data(as_text=True)


def test_api_can_create_poll(sample_user, client):
    payload = login_api(client)
    response = client.post(
        "/api/polls",
        headers={
            "Authorization": f"Bearer {payload['token']}",
            "X-CSRF-Token": payload["csrf_token"],
        },
        json={
            "title": "Roadmap vote",
            "description": "Pick the next task",
            "access_type": "public",
            "poll_type": "multiple",
            "is_anonymous": True,
            "ends_at": "2099-01-01T10:00:00",
            "options": [
                {"text": "One"},
                {"text": "Two"},
            ],
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["poll"]["title"] == "Roadmap vote"
    assert payload["poll"]["poll_type"] == "multiple"
    assert payload["poll"]["is_anonymous"] is True
    assert payload["poll"]["audit_logs"][0]["action"] == "created"
    assert db.session.query(PollAuditLog).count() == 1


def test_user_can_upload_avatar(sample_user, client):
    payload = login_api(client)
    response = client.post(
        "/api/me/avatar",
        headers={
            "Authorization": f"Bearer {payload['token']}",
            "X-CSRF-Token": payload["csrf_token"],
        },
        data={"avatar": (BytesIO(PNG_BYTES), "avatar.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    user = response.get_json()["user"]
    assert user["profile_image"].startswith("/uploads/avatar_")


def test_avatar_upload_rejects_fake_image(sample_user, client):
    payload = login_api(client)
    response = client.post(
        "/api/me/avatar",
        headers={
            "Authorization": f"Bearer {payload['token']}",
            "X-CSRF-Token": payload["csrf_token"],
        },
        data={"avatar": (BytesIO(b"not-an-image"), "avatar.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400


def test_link_poll_is_open_by_code_but_hidden_from_guest_feed(sample_user, client):
    poll = Poll(
        title="Invite only",
        description="Code access",
        poll_type="link",
        is_anonymous=True,
        anonymity_level=2,
        allow_multiple_choices=False,
        is_active=True,
        created_by_id=sample_user.id,
        ends_at=datetime.now(timezone.utc).replace(tzinfo=None).replace(year=2099),
    )
    poll.options = [Option(text="One"), Option(text="Two")]
    db.session.add(poll)
    db.session.commit()

    feed = client.get("/api/polls")
    by_code = client.get(f"/api/polls/{poll.unique_code}")
    by_id = client.get(f"/api/polls/{poll.id}")

    assert feed.status_code == 200
    assert all(item["code"] != poll.unique_code for item in feed.get_json()["polls"])
    assert by_code.status_code == 200
    assert by_code.get_json()["poll"]["code"] == poll.unique_code
    assert by_id.status_code == 404


def test_poll_views_are_counted_once_per_viewer(sample_poll, client):
    first = client.get(f"/api/polls/{sample_poll.unique_code}")
    second = client.get(f"/api/polls/{sample_poll.unique_code}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert db.session.query(PollView).filter_by(poll_id=sample_poll.id).count() == 1
    assert second.get_json()["poll"]["views_count"] == 1


def test_comments_are_added_to_poll(sample_poll, client):
    payload = login_api(client)
    response = client.post(
        f"/api/polls/{sample_poll.unique_code}/comments",
        headers=auth_headers(payload),
        json={"body": "Looks good"},
    )

    assert response.status_code == 201
    assert db.session.query(PollComment).filter_by(poll_id=sample_poll.id).count() == 1
    assert response.get_json()["poll"]["comments"][0]["body"] == "Looks good"


def test_results_publication_can_hide_and_publish(sample_poll, client):
    payload = login_api(client)
    participant = User(username="bob")
    participant.set_password("password123")
    db.session.add(participant)
    db.session.commit()
    participant_payload = login_api(client, "bob")

    hidden = client.post(
        f"/api/polls/{sample_poll.unique_code}/results",
        headers=auth_headers(payload),
        json={"results_visibility": "manual", "results_published": False},
    )
    participant_hidden = client.get(
        f"/api/polls/{sample_poll.unique_code}",
        headers={"Authorization": f"Bearer {participant_payload['token']}"},
    )
    published = client.post(
        f"/api/polls/{sample_poll.unique_code}/results",
        headers=auth_headers(payload),
        json={"results_published": True},
    )
    participant_still_hidden = client.get(
        f"/api/polls/{sample_poll.unique_code}",
        headers={"Authorization": f"Bearer {participant_payload['token']}"},
    )
    vote = client.post(
        f"/api/polls/{sample_poll.unique_code}/vote",
        headers=auth_headers(participant_payload),
        json={"option_ids": [sample_poll.options[0].id]},
    )
    participant_visible = client.get(
        f"/api/polls/{sample_poll.unique_code}",
        headers={"Authorization": f"Bearer {participant_payload['token']}"},
    )

    assert hidden.status_code == 200
    assert hidden.get_json()["poll"]["results_visible"] is True
    assert participant_hidden.get_json()["poll"]["results_visible"] is False
    assert published.status_code == 200
    assert participant_still_hidden.get_json()["poll"]["results_visible"] is False
    assert vote.status_code == 200
    assert vote.get_json()["poll"]["results_visible"] is True
    assert participant_visible.get_json()["poll"]["results_visible"] is True
    assert db.session.query(PollAuditLog).filter_by(poll_id=sample_poll.id).count() == 3


def test_poll_export_csv_and_pdf(sample_poll, client):
    payload = login_api(client)
    csv_response = client.get(
        f"/api/polls/{sample_poll.unique_code}/export.csv",
        headers={"Authorization": f"Bearer {payload['token']}"},
    )
    pdf_response = client.get(
        f"/api/polls/{sample_poll.unique_code}/export.pdf",
        headers={"Authorization": f"Bearer {payload['token']}"},
    )

    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.content_type
    assert "Сводка" in csv_response.get_data(as_text=True)
    assert pdf_response.status_code == 200
    assert pdf_response.content_type == "application/pdf"
    assert pdf_response.data.startswith(b"%PDF")


def test_poll_delete_moves_to_archive(sample_poll, client):
    payload = login_api(client)
    response = client.delete(
        f"/api/polls/{sample_poll.unique_code}",
        headers=auth_headers(payload),
    )
    archived_poll = db.session.get(Poll, sample_poll.id)
    guest_response = client.get(f"/api/polls/{sample_poll.unique_code}")
    owner_feed = client.get(
        "/api/polls",
        headers={"Authorization": f"Bearer {payload['token']}"},
    )

    assert response.status_code == 200
    assert response.get_json()["archived"] is True
    assert archived_poll.is_archived is True
    assert archived_poll.is_active is False
    assert guest_response.status_code == 404
    assert any(item["code"] == sample_poll.unique_code and item["is_archived"] for item in owner_feed.get_json()["polls"])


def test_profile_privacy_hides_participation_from_public(sample_poll, client):
    payload = login_api(client)
    vote_response = client.post(
        f"/api/polls/{sample_poll.unique_code}/vote",
        headers=auth_headers(payload),
        json={"option_ids": [sample_poll.options[0].id]},
    )
    privacy_response = client.patch(
        "/api/me/privacy",
        headers=auth_headers(payload),
        json={"hide_activity": True},
    )
    public_profile = client.get(f"/api/users/{sample_poll.created_by_id}/profile").get_json()["profile"]
    own_profile = client.get(
        f"/api/users/{sample_poll.created_by_id}/profile",
        headers={"Authorization": f"Bearer {payload['token']}"},
    ).get_json()["profile"]

    assert vote_response.status_code == 200
    assert privacy_response.status_code == 200
    assert public_profile["activity_hidden"] is True
    assert public_profile["participation_count"] == 0
    assert public_profile["participated_polls"] == []
    assert own_profile["activity_hidden"] is False
    assert own_profile["participation_count"] == 1
