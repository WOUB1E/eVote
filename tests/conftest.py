from datetime import datetime, timedelta, timezone
import os

import pytest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import RATE_LIMIT_BUCKETS, app
from models import Option, Poll, User, db


@pytest.fixture()
def test_app(tmp_path):
    RATE_LIMIT_BUCKETS.clear()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SECRET_KEY="test-secret-value-that-is-long-enough-for-jwt",
        UPLOAD_FOLDER=str(tmp_path / "uploads"),
    )

    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(test_app):
    return test_app.test_client()


@pytest.fixture()
def sample_user(test_app):
    user = User(username="alice")
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture()
def sample_poll(test_app, sample_user):
    poll = Poll(
        title="Favorite color",
        description="Pick one",
        poll_type="public",
        is_anonymous=False,
        anonymity_level=0,
        allow_multiple_choices=False,
        is_active=True,
        created_by_id=sample_user.id,
        ends_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
    )
    poll.options = [
        Option(text="Red"),
        Option(text="Blue"),
    ]
    db.session.add(poll)
    db.session.commit()
    return poll


@pytest.fixture()
def anonymous_poll(test_app, sample_user):
    poll = Poll(
        title="Anonymous choice",
        description="Hidden votes",
        poll_type="public",
        is_anonymous=True,
        anonymity_level=2,
        allow_multiple_choices=True,
        is_active=True,
        created_by_id=sample_user.id,
        ends_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
    )
    poll.options = [
        Option(text="A"),
        Option(text="B"),
    ]
    db.session.add(poll)
    db.session.commit()
    return poll


@pytest.fixture()
def expired_poll(test_app, sample_user):
    poll = Poll(
        title="Expired",
        description="Closed",
        poll_type="public",
        is_anonymous=False,
        anonymity_level=0,
        allow_multiple_choices=False,
        is_active=True,
        created_by_id=sample_user.id,
        ends_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
    )
    poll.options = [
        Option(text="Yes"),
        Option(text="No"),
    ]
    db.session.add(poll)
    db.session.commit()
    return poll
