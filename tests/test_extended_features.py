import re
from io import BytesIO
from urllib.parse import parse_qs, urlparse

from models import Poll, PollComment, Report, SupportTicket, User, db

PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


def auth_headers(payload):
    return {
        "Authorization": f"Bearer {payload['token']}",
        "X-CSRF-Token": payload["csrf_token"],
    }


def login_api(client, username="alice", password="password123"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.get_json()


def captcha_answer(question):
    match = re.match(r"(\d+) ([+-]) (\d+) = \?", question)
    assert match
    left, operator, right = match.groups()
    return str(int(left) + int(right) if operator == "+" else int(left) - int(right))


def test_registration_requires_consent_and_captcha(client):
    captcha = client.get("/api/captcha").get_json()["captcha"]
    response = client.post(
        "/api/auth/register",
        json={
            "username": "charlie",
            "password": "password123",
            "terms_accepted": True,
            "privacy_accepted": True,
            "captcha_token": captcha["token"],
            "captcha_answer": captcha_answer(captcha["question"]),
        },
    )

    assert response.status_code == 201
    user = response.get_json()["user"]
    assert user["terms_accepted"] is True
    assert user["privacy_accepted"] is True


def test_yandex_auth_redirect_uses_configured_app(test_app, client):
    test_app.config.update(
        YANDEX_CLIENT_ID="client-id",
        YANDEX_CLIENT_SECRET="client-secret",
        YANDEX_REDIRECT_URI="http://127.0.0.1:5000/auth/callback",
        YANDEX_SCOPE="login:email,login:info login:email",
    )

    response = client.get("/auth/yandex")
    location = response.headers["Location"]
    parsed = urlparse(location)
    params = parse_qs(parsed.query)

    assert response.status_code == 302
    assert parsed.scheme == "https"
    assert parsed.netloc == "oauth.yandex.ru"
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["client-id"]
    assert params["redirect_uri"] == ["http://127.0.0.1:5000/auth/callback"]
    assert params["scope"] == ["login:email login:info"]
    assert params["state"][0]


def test_yandex_callback_creates_user(test_app, client, monkeypatch):
    test_app.config.update(
        YANDEX_CLIENT_ID="client-id",
        YANDEX_CLIENT_SECRET="client-secret",
        YANDEX_REDIRECT_URI="http://127.0.0.1:5000/auth/callback",
    )

    def fake_yandex_json_request(url, *, data=None, headers=None):
        if "oauth.yandex" in url:
            assert data["code"] == "auth-code"
            return {"access_token": "access-token"}
        assert headers["Authorization"] == "OAuth access-token"
        return {
            "id": "ya-123",
            "login": "yandex_user",
            "default_email": "user@example.test",
            "first_name": "Ivan",
            "last_name": "Petrov",
            "sex": "male",
            "birthday": "2000-01-02",
            "default_avatar_id": "avatar-id",
        }

    monkeypatch.setattr("app.yandex_json_request", fake_yandex_json_request)
    with client.session_transaction() as sess:
        sess["yandex_oauth_state"] = "state-token"

    response = client.get("/auth/callback?code=auth-code&state=state-token")
    user = User.query.filter_by(yandex_id="ya-123").first()

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/#yandex_auth=")
    assert user is not None
    assert user.email == "user@example.test"
    assert user.birth_date.isoformat() == "2000-01-02"
    assert user.profile_verified is False
    assert user.terms_accepted_at is not None
    assert user.privacy_accepted_at is not None


def test_poll_image_upload_and_single_option_poll(sample_user, client):
    payload = login_api(client)
    upload = client.post(
        "/api/uploads/poll-image",
        headers=auth_headers(payload),
        data={"image": (BytesIO(PNG_BYTES), "poll.png")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    image = upload.get_json()

    response = client.post(
        "/api/polls",
        headers=auth_headers(payload),
        json={
            "title": "Image poll",
            "description": "With picture",
            "description_image": image["filename"],
            "access_type": "public",
            "poll_type": "single",
            "anonymity_level": 2,
            "ends_at": "2099-01-01T10:00:00",
            "options": [{"text": "Only option", "image": image["filename"]}],
        },
    )

    assert response.status_code == 201
    poll = response.get_json()["poll"]
    assert poll["description_image"].startswith("/uploads/")
    assert poll["options"][0]["image"].startswith("/uploads/")
    assert db.session.query(Poll).filter_by(title="Image poll").count() == 1


def test_poll_defaults_to_open_and_can_be_infinite(sample_user, client):
    payload = login_api(client)
    response = client.post(
        "/api/polls",
        headers=auth_headers(payload),
        json={
            "title": "Infinite open poll",
            "description": "No deadline",
            "access_type": "public",
            "poll_type": "single",
            "is_infinite": True,
            "options": [{"text": "Ready"}],
        },
    )

    assert response.status_code == 201
    poll = response.get_json()["poll"]
    stored = db.session.query(Poll).filter_by(title="Infinite open poll").one()
    assert poll["anonymity_level"] == 0
    assert poll["results_visibility"] == "after_end"
    assert poll["is_infinite"] is True
    assert poll["ends_at"] is None
    assert stored.ends_at is None


def test_extensionless_image_upload_is_detected(sample_user, client):
    payload = login_api(client)
    response = client.post(
        "/api/uploads/poll-image",
        headers=auth_headers(payload),
        data={"image": (BytesIO(PNG_BYTES), "upload")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["filename"].endswith(".png")


def test_profile_details_are_readonly_and_username_can_change(sample_user, client):
    payload = login_api(client)
    readonly_response = client.patch(
        "/api/me/details",
        headers=auth_headers(payload),
        json={"birth_date": "2004-05-20", "gender": "male", "city": "Красноярск"},
    )
    username_response = client.patch(
        "/api/me/username",
        headers=auth_headers(payload),
        json={"username": "alice_new"},
    )

    assert readonly_response.status_code == 403
    assert username_response.status_code == 200
    user = username_response.get_json()["user"]
    assert user["username"] == "alice_new"
    assert db.session.get(User, sample_user.id).username == "alice_new"


def test_admin_can_block_user_and_delete_content(sample_poll, client):
    admin = User(username="admin2", role="admin")
    admin.set_password("password123")
    comment = PollComment(poll_id=sample_poll.id, user_id=sample_poll.created_by_id, body="spam")
    db.session.add_all([admin, comment])
    db.session.commit()

    admin_payload = login_api(client, "admin2")
    user_response = client.patch(
        f"/api/users/{sample_poll.created_by_id}/block",
        headers=auth_headers(admin_payload),
        json={"blocked": True},
    )
    comment_response = client.delete(f"/api/comments/{comment.id}", headers=auth_headers(admin_payload))
    poll_response = client.delete(f"/api/polls/{sample_poll.unique_code}?hard=1", headers=auth_headers(admin_payload))

    assert user_response.status_code == 200
    assert user_response.get_json()["user"]["is_blocked"] is True
    assert comment_response.status_code == 200
    assert poll_response.status_code == 200
    assert db.session.get(PollComment, comment.id) is None
    assert db.session.get(Poll, sample_poll.id) is None


def test_reports_and_support_are_visible_to_admin(sample_poll, client):
    admin = User(username="moderator", role="admin")
    admin.set_password("password123")
    db.session.add(admin)
    db.session.commit()

    user_payload = login_api(client)
    admin_payload = login_api(client, "moderator")

    report_response = client.post(
        "/api/reports",
        headers=auth_headers(user_payload),
        json={"target_type": "poll", "target_id": sample_poll.id, "reason": "Нарушение правил", "body": "Проверить"},
    )
    ticket_response = client.post(
        "/api/support",
        headers=auth_headers(user_payload),
        json={"subject": "Нужна помощь", "body": "Не могу найти результаты"},
    )
    reports_response = client.get("/api/admin/reports", headers={"Authorization": f"Bearer {admin_payload['token']}"})
    support_response = client.get("/api/admin/support", headers={"Authorization": f"Bearer {admin_payload['token']}"})

    assert report_response.status_code == 201
    assert ticket_response.status_code == 201
    assert reports_response.status_code == 200
    assert support_response.status_code == 200
    assert db.session.query(Report).count() == 1
    assert db.session.query(SupportTicket).count() == 1
