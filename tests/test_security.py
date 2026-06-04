import base64
import json

from app import RATE_LIMIT_BUCKETS
from models import User, VoterChoice, VoterLog, db


def auth_headers(login_payload):
    return {
        "Authorization": f"Bearer {login_payload['token']}",
        "X-CSRF-Token": login_payload["csrf_token"],
    }


def login_api(client, username="alice", password="password123"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.get_json()


def decode_urlsafe_payload(value):
    padded = value + "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))


def test_password_is_hashed(sample_user):
    assert sample_user.password_hash != "password123"
    assert sample_user.password_hash.startswith("$argon2")
    assert sample_user.check_password("password123")
    assert not sample_user.check_password("wrong")


def test_private_api_rejects_missing_token(client):
    response = client.post("/api/polls")
    assert response.status_code == 401


def test_private_api_rejects_missing_csrf(client, sample_user):
    login_payload = login_api(client)
    response = client.post(
        "/api/polls",
        headers={"Authorization": f"Bearer {login_payload['token']}"},
        json={
            "title": "New poll",
            "description": "Test",
            "access_type": "public",
            "poll_type": "single",
            "is_anonymous": True,
            "ends_at": "2099-01-01T10:00:00",
            "options": [{"text": "A"}, {"text": "B"}],
        },
    )
    assert response.status_code == 403


def test_security_headers_are_sent(client):
    response = client.get("/api/health")

    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"


def test_vk_callback_is_not_checked_as_yandex_state(client):
    response = client.get("/auth/callback?code=vk-code&device_id=vk-device&state=vk-state&type=code_v2")
    payload = decode_urlsafe_payload(response.headers["Location"].split("#vk_auth=", 1)[1])

    assert response.status_code == 302
    assert "/#vk_auth=" in response.headers["Location"]
    assert payload["code"] == "vk-code"
    assert payload["device_id"] == "vk-device"
    assert payload["state"] == "vk-state"
    assert payload["type"] == "code_v2"


def test_dedicated_vk_callback_redirects_to_frontend(client):
    response = client.get("/auth/vk/callback?code=vk-code&device_id=vk-device")

    assert response.status_code == 302
    assert "/#vk_auth=" in response.headers["Location"]


def test_auth_rate_limit_throttles_repeated_requests(client, test_app):
    previous_limits = dict(test_app.config.get("RATE_LIMITS", {}))
    previous_window = test_app.config.get("RATE_LIMIT_WINDOW_SECONDS")
    previous_enabled = test_app.config.get("RATE_LIMIT_ENABLED")
    RATE_LIMIT_BUCKETS.clear()
    test_app.config["RATE_LIMIT_ENABLED"] = True
    test_app.config["RATE_LIMIT_WINDOW_SECONDS"] = 60
    test_app.config["RATE_LIMITS"] = {**previous_limits, "auth": 2}

    try:
        first = client.post("/api/auth/login", json={"username": "missing", "password": "wrong"})
        second = client.post("/api/auth/login", json={"username": "missing", "password": "wrong"})
        third = client.post("/api/auth/login", json={"username": "missing", "password": "wrong"})

        assert first.status_code == 401
        assert second.status_code == 401
        assert third.status_code == 429
        assert "Retry-After" in third.headers
    finally:
        test_app.config["RATE_LIMITS"] = previous_limits
        test_app.config["RATE_LIMIT_WINDOW_SECONDS"] = previous_window
        test_app.config["RATE_LIMIT_ENABLED"] = previous_enabled
        RATE_LIMIT_BUCKETS.clear()
