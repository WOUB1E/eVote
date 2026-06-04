from datetime import date, datetime, timezone
import secrets
import string

from flask_sqlalchemy import SQLAlchemy
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import CheckConstraint, UniqueConstraint, func
from werkzeug.security import check_password_hash


db = SQLAlchemy()
password_hasher = PasswordHasher()


def generate_unique_code() -> str:
    """Генерирует случайный код из букв и цифр длиной 12 символов."""
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(12))


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")
    profile_image = db.Column(db.String(255), nullable=True)
    yandex_id = db.Column(db.String(64), unique=True, nullable=True, index=True)
    vk_id = db.Column(db.String(64), unique=True, nullable=True, index=True)
    email = db.Column(db.String(255), nullable=True)
    first_name = db.Column(db.String(120), nullable=True)
    last_name = db.Column(db.String(120), nullable=True)
    yandex_avatar_url = db.Column(db.String(500), nullable=True)
    hide_activity = db.Column(db.Boolean, nullable=False, default=False)
    is_blocked = db.Column(db.Boolean, nullable=False, default=False)
    blocked_at = db.Column(db.DateTime, nullable=True)
    birth_date = db.Column(db.Date, nullable=True)
    gender = db.Column(db.String(20), nullable=True)
    city = db.Column(db.String(120), nullable=True)
    terms_accepted_at = db.Column(db.DateTime, nullable=True)
    privacy_accepted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)

    polls = db.relationship("Poll", back_populates="creator", lazy=True)
    voter_logs = db.relationship("VoterLog", back_populates="user", lazy=True)
    reports = db.relationship("Report", back_populates="reporter", foreign_keys="Report.reporter_id", lazy=True)
    support_tickets = db.relationship("SupportTicket", back_populates="user", lazy=True)

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
    )

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def profile_verified(self) -> bool:
        return bool(self.birth_date and self.gender and self.city)

    def set_password(self, password: str) -> None:
        # Пароль никогда не хранится в открытом виде.
        self.password_hash = password_hasher.hash(password)

    def check_password(self, password: str) -> bool:
        if self.password_hash.startswith("$argon2"):
            try:
                return password_hasher.verify(self.password_hash, password)
            except (InvalidHashError, VerificationError, VerifyMismatchError):
                return False

        # Совместимость со старыми хешами Werkzeug из прежней версии проекта.
        return check_password_hash(self.password_hash, password)


class Poll(db.Model):
    __tablename__ = "polls"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    description_image = db.Column(db.String(255), nullable=True)
    description_images = db.Column(db.Text, nullable=True)
    unique_code = db.Column(db.String(12), unique=True, nullable=False, default=generate_unique_code, index=True)
    poll_type = db.Column(db.String(20), nullable=False, default="link")  # link, limited, public
    max_votes = db.Column(db.Integer, nullable=True)  # для poll_type='limited'
    anonymity_level = db.Column(db.Integer, nullable=False, default=0)  # 0: полная гласность, 1: скрыто кто за что, 2: полная анонимность
    allow_multiple_choices = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)  # True = активен, False = завершен
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)
    ends_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    archived_at = db.Column(db.DateTime, nullable=True)
    results_visibility = db.Column(db.String(20), nullable=False, default="after_end")
    results_published = db.Column(db.Boolean, nullable=False, default=False)

    creator = db.relationship("User", back_populates="polls")
    options = db.relationship(
        "Option",
        back_populates="poll",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="Option.id",
    )
    voter_logs = db.relationship(
        "VoterLog",
        back_populates="poll",
        cascade="all, delete-orphan",
        lazy=True,
    )
    anonymous_ballots = db.relationship(
        "AnonymousBallot",
        back_populates="poll",
        cascade="all, delete-orphan",
        lazy=True,
    )
    comments = db.relationship(
        "PollComment",
        back_populates="poll",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="PollComment.created_at.desc()",
    )
    view_records = db.relationship(
        "PollView",
        back_populates="poll",
        cascade="all, delete-orphan",
        lazy=True,
    )
    audit_logs = db.relationship(
        "PollAuditLog",
        back_populates="poll",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="PollAuditLog.created_at.desc()",
    )

    __table_args__ = (
        CheckConstraint("poll_type IN ('link', 'limited', 'public')", name="ck_polls_poll_type"),
        CheckConstraint("anonymity_level IN (0, 1, 2)", name="ck_polls_anonymity_level"),
        CheckConstraint("results_visibility IN ('always', 'after_end', 'manual', 'hidden')", name="ck_polls_results_visibility"),
    )

    @property
    def is_anonymous(self) -> bool:
        return self.anonymity_level != 0

    @is_anonymous.setter
    def is_anonymous(self, value: bool) -> None:
        self.anonymity_level = 2 if bool(value) else 0

    @property
    def can_vote(self) -> bool:
        """Можно ли голосовать в этом опросе."""
        if not self.is_active:
            return False

        if self.has_ended:
            return False
        
        if self.poll_type == "limited" and self.max_votes:
            return self.voters_count < self.max_votes
        
        return True

    @property
    def has_ended(self) -> bool:
        return bool(self.ends_at and datetime.now(timezone.utc).replace(tzinfo=None) >= self.ends_at)

    @property
    def total_votes(self) -> int:
        public_choices = (
            db.session.query(func.count(VoterChoice.id))
            .join(VoterLog, VoterChoice.voter_log_id == VoterLog.id)
            .filter(VoterLog.poll_id == self.id)
            .scalar()
            or 0
        )
        anonymous_choices = (
            db.session.query(func.count(AnonymousBallotChoice.id))
            .join(AnonymousBallot, AnonymousBallotChoice.ballot_id == AnonymousBallot.id)
            .filter(AnonymousBallot.poll_id == self.id)
            .scalar()
            or 0
        )
        return public_choices + anonymous_choices

    @property
    def voters_count(self) -> int:
        return len(self.voter_logs)

    @property
    def views_count(self) -> int:
        return len(self.view_records)


class Option(db.Model):
    __tablename__ = "options"

    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("polls.id"), nullable=False)
    text = db.Column(db.String(255), nullable=False)
    image_url = db.Column(db.String(500), nullable=True)  # внешняя ссылка на изображение
    image = db.Column(db.String(255), nullable=True)  # имя загруженного файла с изображением
    images = db.Column(db.Text, nullable=True)

    poll = db.relationship("Poll", back_populates="options")
    public_choices = db.relationship("VoterChoice", back_populates="option", lazy=True)
    anonymous_choices = db.relationship("AnonymousBallotChoice", back_populates="option", lazy=True)

    @property
    def votes_count(self) -> int:
        public_choices = (
            db.session.query(func.count(VoterChoice.id))
            .join(VoterLog, VoterChoice.voter_log_id == VoterLog.id)
            .filter(VoterChoice.option_id == self.id, VoterLog.poll_id == self.poll_id)
            .scalar()
            or 0
        )
        anonymous_choices = (
            db.session.query(func.count(AnonymousBallotChoice.id))
            .join(AnonymousBallot, AnonymousBallotChoice.ballot_id == AnonymousBallot.id)
            .filter(AnonymousBallotChoice.option_id == self.id, AnonymousBallot.poll_id == self.poll_id)
            .scalar()
            or 0
        )
        return public_choices + anonymous_choices


class VoterLog(db.Model):
    __tablename__ = "voter_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    poll_id = db.Column(db.Integer, db.ForeignKey("polls.id"), nullable=False)
    voted_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)

    user = db.relationship("User", back_populates="voter_logs")
    poll = db.relationship("Poll", back_populates="voter_logs")
    selected_options = db.relationship(
        "VoterChoice",
        backref="voter_log",
        lazy=True,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "poll_id", name="uq_voter_logs_user_poll"),
    )


class VoterChoice(db.Model):
    __tablename__ = "voter_choices"

    id = db.Column(db.Integer, primary_key=True)
    voter_log_id = db.Column(db.Integer, db.ForeignKey("voter_logs.id"), nullable=False)
    option_id = db.Column(db.Integer, db.ForeignKey("options.id"), nullable=False)

    option = db.relationship("Option", back_populates="public_choices")

    __table_args__ = (
        UniqueConstraint("voter_log_id", "option_id", name="uq_voter_choices"),
    )


class AnonymousBallot(db.Model):
    __tablename__ = "anonymous_ballots"

    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("polls.id"), nullable=False)
    cast_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)

    poll = db.relationship("Poll", back_populates="anonymous_ballots")
    choices = db.relationship(
        "AnonymousBallotChoice",
        back_populates="ballot",
        cascade="all, delete-orphan",
        lazy=True,
    )


class AnonymousBallotChoice(db.Model):
    __tablename__ = "anonymous_ballot_choices"

    id = db.Column(db.Integer, primary_key=True)
    ballot_id = db.Column(db.Integer, db.ForeignKey("anonymous_ballots.id"), nullable=False)
    option_id = db.Column(db.Integer, db.ForeignKey("options.id"), nullable=False)

    ballot = db.relationship("AnonymousBallot", back_populates="choices")
    option = db.relationship("Option", back_populates="anonymous_choices")

    __table_args__ = (
        UniqueConstraint("ballot_id", "option_id", name="uq_anonymous_ballot_choices"),
    )


class PollComment(db.Model):
    __tablename__ = "poll_comments"

    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("polls.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)

    poll = db.relationship("Poll", back_populates="comments")
    user = db.relationship("User")


class PollView(db.Model):
    __tablename__ = "poll_views"

    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("polls.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    viewer_key = db.Column(db.String(96), nullable=False)
    viewed_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)

    poll = db.relationship("Poll", back_populates="view_records")
    user = db.relationship("User")

    __table_args__ = (
        UniqueConstraint("poll_id", "viewer_key", name="uq_poll_views_poll_viewer"),
    )


class PollAuditLog(db.Model):
    __tablename__ = "poll_audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("polls.id"), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String(60), nullable=False)
    category = db.Column(db.String(20), nullable=False, default="change")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)

    poll = db.relationship("Poll", back_populates="audit_logs")
    actor = db.relationship("User")
    snapshot_record = db.relationship(
        "PollAuditSnapshot",
        back_populates="audit_log",
        uselist=False,
        cascade="all, delete-orphan",
        lazy=True,
    )
    change_records = db.relationship(
        "PollAuditChange",
        back_populates="audit_log",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="PollAuditChange.id",
    )
    vote_detail = db.relationship(
        "PollAuditVoteDetail",
        back_populates="audit_log",
        uselist=False,
        cascade="all, delete-orphan",
        lazy=True,
    )
    vote_options = db.relationship(
        "PollAuditVoteOption",
        back_populates="audit_log",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="PollAuditVoteOption.id",
    )


class PollAuditSnapshot(db.Model):
    __tablename__ = "poll_audit_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    audit_log_id = db.Column(db.Integer, db.ForeignKey("poll_audit_logs.id"), nullable=False, unique=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    access_type = db.Column(db.String(20), nullable=False)
    selection_type = db.Column(db.String(20), nullable=False)
    anonymity_level = db.Column(db.Integer, nullable=False)
    max_votes = db.Column(db.Integer, nullable=True)
    ends_at = db.Column(db.String(32), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False)
    is_archived = db.Column(db.Boolean, nullable=False)
    completed_at = db.Column(db.String(32), nullable=True)
    archived_at = db.Column(db.String(32), nullable=True)
    results_visibility = db.Column(db.String(20), nullable=False)
    results_published = db.Column(db.Boolean, nullable=False)

    audit_log = db.relationship("PollAuditLog", back_populates="snapshot_record")
    options = db.relationship(
        "PollAuditSnapshotOption",
        back_populates="snapshot",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="PollAuditSnapshotOption.position",
    )


class PollAuditSnapshotOption(db.Model):
    __tablename__ = "poll_audit_snapshot_options"

    id = db.Column(db.Integer, primary_key=True)
    audit_log_id = db.Column(db.Integer, db.ForeignKey("poll_audit_snapshots.audit_log_id"), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    text = db.Column(db.String(255), nullable=False)

    snapshot = db.relationship("PollAuditSnapshot", back_populates="options")

    __table_args__ = (
        UniqueConstraint("audit_log_id", "position", name="uq_poll_audit_snapshot_options_position"),
    )


class PollAuditChange(db.Model):
    __tablename__ = "poll_audit_changes"

    id = db.Column(db.Integer, primary_key=True)
    audit_log_id = db.Column(db.Integer, db.ForeignKey("poll_audit_logs.id"), nullable=False)
    field = db.Column(db.String(60), nullable=False)
    label = db.Column(db.String(120), nullable=False)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)

    audit_log = db.relationship("PollAuditLog", back_populates="change_records")


class PollAuditVoteDetail(db.Model):
    __tablename__ = "poll_audit_vote_details"

    id = db.Column(db.Integer, primary_key=True)
    audit_log_id = db.Column(db.Integer, db.ForeignKey("poll_audit_logs.id"), nullable=False, unique=True)
    anonymity_level = db.Column(db.Integer, nullable=False)
    voter_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    voter_hidden = db.Column(db.Boolean, nullable=False, default=False)
    choice_hidden = db.Column(db.Boolean, nullable=False, default=False)

    audit_log = db.relationship("PollAuditLog", back_populates="vote_detail")
    voter = db.relationship("User")


class PollAuditVoteOption(db.Model):
    __tablename__ = "poll_audit_vote_options"

    id = db.Column(db.Integer, primary_key=True)
    audit_log_id = db.Column(db.Integer, db.ForeignKey("poll_audit_logs.id"), nullable=False)
    option_id = db.Column(db.Integer, db.ForeignKey("options.id"), nullable=True)
    option_text = db.Column(db.String(255), nullable=False)

    audit_log = db.relationship("PollAuditLog", back_populates="vote_options")
    option = db.relationship("Option")


class Report(db.Model):
    __tablename__ = "reports"

    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    target_type = db.Column(db.String(20), nullable=False)
    target_id = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(80), nullable=False)
    body = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending")
    admin_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    admin_note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    reporter = db.relationship("User", foreign_keys=[reporter_id], back_populates="reports")
    admin = db.relationship("User", foreign_keys=[admin_id])

    __table_args__ = (
        CheckConstraint("target_type IN ('poll', 'comment', 'user')", name="ck_reports_target_type"),
        CheckConstraint("status IN ('pending', 'reviewing', 'resolved', 'rejected')", name="ck_reports_status"),
    )


class SupportTicket(db.Model):
    __tablename__ = "support_tickets"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    subject = db.Column(db.String(160), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="open")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)

    user = db.relationship("User", back_populates="support_tickets")
    messages = db.relationship(
        "SupportMessage",
        back_populates="ticket",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="SupportMessage.created_at",
    )

    __table_args__ = (
        CheckConstraint("status IN ('open', 'answered', 'closed')", name="ck_support_tickets_status"),
    )


class SupportMessage(db.Model):
    __tablename__ = "support_messages"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("support_tickets.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)

    ticket = db.relationship("SupportTicket", back_populates="messages")
    sender = db.relationship("User")
