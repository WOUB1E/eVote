from datetime import datetime, timezone

from models import AnonymousBallot, AnonymousBallotChoice, Option, Poll, PollAuditLog, User, VoterChoice, VoterLog, db


def login_api(client, username="alice", password="password123"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.get_json()


def auth_headers(payload):
    return {
        "Authorization": f"Bearer {payload['token']}",
        "X-CSRF-Token": payload["csrf_token"],
    }


def test_public_vote_records_public_vote(sample_poll, client, test_app):
    payload = login_api(client)

    response = client.post(
        f"/api/polls/{sample_poll.unique_code}/vote",
        headers=auth_headers(payload),
        json={"option_ids": [sample_poll.options[0].id]},
    )

    assert response.status_code == 200
    assert db.session.query(VoterLog).filter_by(poll_id=sample_poll.id).count() == 1
    assert db.session.query(VoterChoice).count() == 1
    assert db.session.query(AnonymousBallot).filter_by(poll_id=sample_poll.id).count() == 0
    assert db.session.query(PollAuditLog).filter_by(poll_id=sample_poll.id, category="vote").count() == 1
    assert db.session.query(Option).filter_by(id=sample_poll.options[0].id).one().votes_count == 1


def test_anonymous_vote_keeps_vote_log_separate(anonymous_poll, client):
    payload = login_api(client)

    response = client.post(
        f"/api/polls/{anonymous_poll.unique_code}/vote",
        headers=auth_headers(payload),
        json={"option_ids": [anonymous_poll.options[0].id, anonymous_poll.options[1].id]},
    )

    assert response.status_code == 200
    data = response.get_json()["poll"]
    assert data["participant_names_visible"] is False
    assert data["participation_log"] == []
    assert data["total_votes"] == 1
    assert data["choices_count"] == 2
    assert db.session.query(VoterLog).filter_by(poll_id=anonymous_poll.id).count() == 1
    assert db.session.query(VoterChoice).filter(VoterChoice.voter_log_id.in_(
        db.session.query(VoterLog.id).filter_by(poll_id=anonymous_poll.id)
    )).count() == 0
    assert db.session.query(AnonymousBallot).filter_by(poll_id=anonymous_poll.id).count() == 1
    assert db.session.query(AnonymousBallotChoice).join(AnonymousBallot).filter(AnonymousBallot.poll_id == anonymous_poll.id).count() == 2
    assert db.session.query(Option).filter_by(id=anonymous_poll.options[0].id).one().votes_count == 1
    assert db.session.query(Option).filter_by(id=anonymous_poll.options[1].id).one().votes_count == 1


def test_semi_anonymous_vote_shows_participant_without_choice(sample_user, client):
    poll = Poll(
        title="Semi anonymous",
        description="Participants visible",
        poll_type="public",
        is_anonymous=True,
        anonymity_level=1,
        allow_multiple_choices=False,
        is_active=True,
        created_by_id=sample_user.id,
        ends_at=datetime.now(timezone.utc).replace(tzinfo=None).replace(year=2099),
    )
    poll.options = [Option(text="Alpha"), Option(text="Beta")]
    db.session.add(poll)
    db.session.commit()
    option_id = poll.options[0].id

    payload = login_api(client)
    response = client.post(
        f"/api/polls/{poll.unique_code}/vote",
        headers=auth_headers(payload),
        json={"option_ids": [option_id]},
    )

    assert response.status_code == 200
    data = response.get_json()["poll"]
    assert data["results_visible"] is True
    assert data["participant_names_visible"] is True
    assert data["public_votes"] == []
    assert data["participation_log"][0]["user"] == "alice"
    assert db.session.query(VoterLog).filter_by(poll_id=poll.id).count() == 1
    assert db.session.query(VoterChoice).count() == 0
    assert db.session.query(AnonymousBallot).filter_by(poll_id=poll.id).count() == 1
    assert db.session.query(Option).filter_by(id=option_id).one().votes_count == 1


def test_limited_poll_blocks_after_max_voters(sample_user, client):
    poll = Poll(
        title="Limited",
        description="Only one voter",
        poll_type="limited",
        max_votes=1,
        is_anonymous=False,
        anonymity_level=0,
        allow_multiple_choices=False,
        is_active=True,
        created_by_id=sample_user.id,
        ends_at=datetime.now(timezone.utc).replace(tzinfo=None).replace(year=2099),
    )
    poll.options = [Option(text="Yes"), Option(text="No")]
    bob = User(username="bob")
    bob.set_password("password123")
    db.session.add_all([poll, bob])
    db.session.commit()

    alice_payload = login_api(client)
    first = client.post(
        f"/api/polls/{poll.unique_code}/vote",
        headers=auth_headers(alice_payload),
        json={"option_ids": [poll.options[0].id]},
    )
    bob_payload = login_api(client, "bob", "password123")
    second = client.post(
        f"/api/polls/{poll.unique_code}/vote",
        headers=auth_headers(bob_payload),
        json={"option_ids": [poll.options[1].id]},
    )

    assert first.status_code == 200
    assert second.status_code == 400
    assert db.session.query(VoterLog).filter_by(poll_id=poll.id).count() == 1
    assert db.session.query(Option).filter_by(id=poll.options[1].id).one().votes_count == 0


def test_repeat_vote_is_blocked(sample_poll, client):
    payload = login_api(client)

    first = client.post(
        f"/api/polls/{sample_poll.unique_code}/vote",
        headers=auth_headers(payload),
        json={"option_ids": [sample_poll.options[0].id]},
    )
    second = client.post(
        f"/api/polls/{sample_poll.unique_code}/vote",
        headers=auth_headers(payload),
        json={"option_ids": [sample_poll.options[1].id]},
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert db.session.query(VoterLog).filter_by(poll_id=sample_poll.id).count() == 1
    assert db.session.query(Option).filter_by(id=sample_poll.options[1].id).one().votes_count == 0


def test_expired_poll_cannot_be_voted(sample_user, client, test_app):
    poll = Poll(
        title="Expired",
        description="Closed",
        poll_type="public",
        is_anonymous=False,
        anonymity_level=0,
        allow_multiple_choices=False,
        is_active=True,
        created_by_id=sample_user.id,
        ends_at=datetime.now(timezone.utc).replace(tzinfo=None).replace(year=2000),
    )
    poll.options = [Option(text="Yes"), Option(text="No")]
    db.session.add(poll)
    db.session.commit()

    payload = login_api(client)
    response = client.post(
        f"/api/polls/{poll.unique_code}/vote",
        headers=auth_headers(payload),
        json={"option_ids": [poll.options[0].id]},
    )

    assert response.status_code == 400
    assert db.session.query(VoterLog).filter_by(poll_id=poll.id).count() == 0


def test_api_vote_requires_csrf_and_blocks_repeat(sample_poll, client):
    payload = login_api(client)
    response = client.post(
        f"/api/polls/{sample_poll.unique_code}/vote",
        headers={"Authorization": f"Bearer {payload['token']}"},
        json={"option_ids": [sample_poll.options[0].id]},
    )
    assert response.status_code == 403

    response = client.post(
        f"/api/polls/{sample_poll.unique_code}/vote",
        headers=auth_headers(payload),
        json={"option_ids": [sample_poll.options[0].id]},
    )
    assert response.status_code == 200

    repeat = client.post(
        f"/api/polls/{sample_poll.unique_code}/vote",
        headers=auth_headers(payload),
        json={"option_ids": [sample_poll.options[1].id]},
    )
    assert repeat.status_code == 409
