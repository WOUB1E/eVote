from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from time import monotonic
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen
import base64
import csv
import hashlib
import io
import json
import secrets

import click
import jwt
from flask import Flask, abort, jsonify, redirect, request, send_file, send_from_directory, session
from flask.cli import with_appcontext
from jwt import ExpiredSignatureError, InvalidTokenError
from sqlalchemy import func, inspect, or_, text
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from config import Config
from models import (
    AnonymousBallot,
    AnonymousBallotChoice,
    Option,
    Poll,
    PollAuditChange,
    PollAuditLog,
    PollAuditSnapshot,
    PollAuditSnapshotOption,
    PollAuditVoteDetail,
    PollAuditVoteOption,
    PollComment,
    PollView,
    Report,
    SupportMessage,
    SupportTicket,
    User,
    VoterChoice,
    VoterLog,
    db,
    generate_unique_code,
)


BASE_DIR = Path(__file__).resolve().parent
CLIENT_DIR = BASE_DIR / "static" / "react"
MIN_OPTIONS = 1
MAX_OPTIONS = 50
MAX_IMAGES_PER_FIELD = 5
JWT_ALGORITHM = "HS256"
JWT_TTL = timedelta(hours=12)
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60
DEFAULT_RATE_LIMITS = {
    "auth": 120,
    "write": 300,
    "vote": 180,
    "export": 120,
}
IMAGE_EXTENSION_ALIASES = {
    "png": {"png"},
    "jpg": {"jpeg"},
    "jpeg": {"jpeg"},
    "gif": {"gif"},
    "webp": {"webp"},
}
YANDEX_AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
YANDEX_TOKEN_URL = "https://oauth.yandex.ru/token"
YANDEX_USER_INFO_URL = "https://login.yandex.ru/info"
CLOUDINARY_REF_PREFIX = "cloudinary:"
RATE_LIMIT_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
RLS_TABLES = (
    "users",
    "polls",
    "options",
    "voter_logs",
    "voter_choices",
    "anonymous_ballots",
    "anonymous_ballot_choices",
    "poll_comments",
    "poll_views",
    "poll_audit_logs",
    "poll_audit_snapshots",
    "poll_audit_snapshot_options",
    "poll_audit_changes",
    "poll_audit_vote_details",
    "poll_audit_vote_options",
    "reports",
    "support_tickets",
    "support_messages",
)


app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config.from_object(Config)
db.init_app(app)


def api_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def format_datetime(value: Optional[datetime], *, utc: bool = True) -> Optional[str]:
    if not value:
        return None
    if not utc:
        return value.isoformat(timespec="seconds")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def format_date(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None


def parse_date(value: object) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def age_from_birth_date(value: Optional[date]) -> Optional[int]:
    if value is None:
        return None
    today = date.today()
    years = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
    return max(years, 0)


def parse_datetime(value: object) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def api_json(message: str, status_code: int = 400, **extra):
    payload = {"error": message}
    payload.update(extra)
    return jsonify(payload), status_code


def get_rate_limits() -> dict[str, int]:
    configured = app.config.get("RATE_LIMITS") or {}
    return {**DEFAULT_RATE_LIMITS, **configured}


def rate_limit_identity() -> str:
    payload = get_api_payload()
    if payload and payload.get("sub"):
        return f"user:{payload['sub']}"
    raw = f"{request.remote_addr or ''}|{request.headers.get('User-Agent', '')}"
    digest = hashlib.sha256(f"{app.config['SECRET_KEY']}|{raw}".encode("utf-8")).hexdigest()
    return f"anon:{digest[:32]}"


def rate_limit_error(scope: str) -> Optional[tuple]:
    if not app.config.get("RATE_LIMIT_ENABLED", True):
        return None

    limit = get_rate_limits().get(scope)
    if not limit:
        return None

    window = int(app.config.get("RATE_LIMIT_WINDOW_SECONDS", DEFAULT_RATE_LIMIT_WINDOW_SECONDS))
    now = monotonic()
    bucket_key = f"{scope}:{rate_limit_identity()}"
    bucket = RATE_LIMIT_BUCKETS[bucket_key]

    while bucket and now - bucket[0] >= window:
        bucket.popleft()

    if len(bucket) >= limit:
        retry_after = max(1, int(window - (now - bucket[0])))
        response, status_code = api_json("Слишком много запросов. Попробуйте позже.", 429)
        response.headers["Retry-After"] = str(retry_after)
        return response, status_code

    bucket.append(now)
    return None


def require_rate_limit(scope: str):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            limit_error = rate_limit_error(scope)
            if limit_error is not None:
                return limit_error
            return view_func(*args, **kwargs)

        return wrapper

    return decorator


@app.after_request
def add_security_headers(response):
    if not app.config.get("SECURITY_HEADERS_ENABLED", True):
        return response

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https: http:; "
        "font-src 'self' data:; "
        "connect-src 'self' https://id.vk.ru https://login.vk.com https://api.vk.com; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'",
    )
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")

    return response


def make_jwt_token(user: User) -> tuple[str, str]:
    csrf_token = secrets.token_urlsafe(24)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "csrf": csrf_token,
        "iat": api_now(),
        "exp": api_now() + JWT_TTL,
    }
    token = jwt.encode(payload, app.config["SECRET_KEY"], algorithm=JWT_ALGORITHM)
    return token, csrf_token


def decode_jwt_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(
            token,
            app.config["SECRET_KEY"],
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp", "iat"]},
        )
    except (ExpiredSignatureError, InvalidTokenError):
        return None


def get_bearer_token() -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return header.removeprefix("Bearer ").strip() or None


def get_api_payload() -> Optional[dict]:
    token = get_bearer_token()
    return decode_jwt_token(token) if token else None


def get_api_user() -> Optional[User]:
    payload = get_api_payload()
    if not payload:
        return None
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    return db.session.get(User, user_id)


def require_api_auth(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user = get_api_user()
        if user is None:
            return api_json("Authentication required", 401)
        request.api_user = user  # type: ignore[attr-defined]
        return view_func(*args, **kwargs)

    return wrapper


def require_api_csrf() -> Optional[tuple]:
    payload = get_api_payload()
    if payload is None:
        return api_json("Authentication required", 401)
    if request.headers.get("X-CSRF-Token") != payload.get("csrf"):
        return api_json("CSRF token required", 403)
    return None


def require_write_auth(view_func):
    @wraps(view_func)
    @require_api_auth
    def wrapper(*args, **kwargs):
        user = request.api_user  # type: ignore[attr-defined]
        if user.is_blocked:
            return api_json("Аккаунт заблокирован администратором.", 403)
        csrf_error = require_api_csrf()
        if csrf_error is not None:
            return csrf_error
        limit_error = rate_limit_error("write")
        if limit_error is not None:
            return limit_error
        return view_func(*args, **kwargs)

    return wrapper


def require_admin(view_func):
    @wraps(view_func)
    @require_write_auth
    def wrapper(*args, **kwargs):
        user = request.api_user  # type: ignore[attr-defined]
        if not user.is_admin:
            return api_json("Admin role required", 403)
        return view_func(*args, **kwargs)

    return wrapper


def require_admin_read(view_func):
    @wraps(view_func)
    @require_api_auth
    def wrapper(*args, **kwargs):
        user = request.api_user  # type: ignore[attr-defined]
        if not user.is_admin:
            return api_json("Admin role required", 403)
        return view_func(*args, **kwargs)

    return wrapper


def get_poll_by_ref_or_404(poll_ref: str) -> Poll:
    poll = db.session.get(Poll, int(poll_ref)) if poll_ref.isdigit() else None
    if poll is None:
        poll = Poll.query.filter_by(unique_code=poll_ref).first()
    if poll is None:
        abort(404)
    return poll


def user_has_voted(user_id: int, poll_id: int) -> bool:
    return VoterLog.query.filter_by(user_id=user_id, poll_id=poll_id).first() is not None


def public_choice_records(poll: Poll) -> list[VoterChoice]:
    return (
        VoterChoice.query.join(VoterLog, VoterChoice.voter_log_id == VoterLog.id)
        .filter(VoterLog.poll_id == poll.id)
        .order_by(VoterLog.voted_at.desc(), VoterChoice.id.desc())
        .all()
    )


def poll_option_vote_counts(poll: Poll) -> dict[int, int]:
    counts = {option.id: 0 for option in poll.options}
    public_counts = (
        db.session.query(VoterChoice.option_id, func.count(VoterChoice.id))
        .join(VoterLog, VoterChoice.voter_log_id == VoterLog.id)
        .filter(VoterLog.poll_id == poll.id)
        .group_by(VoterChoice.option_id)
        .all()
    )
    anonymous_counts = (
        db.session.query(AnonymousBallotChoice.option_id, func.count(AnonymousBallotChoice.id))
        .join(AnonymousBallot, AnonymousBallotChoice.ballot_id == AnonymousBallot.id)
        .filter(AnonymousBallot.poll_id == poll.id)
        .group_by(AnonymousBallotChoice.option_id)
        .all()
    )
    for option_id, value in public_counts:
        counts[option_id] = counts.get(option_id, 0) + int(value)
    for option_id, value in anonymous_counts:
        counts[option_id] = counts.get(option_id, 0) + int(value)
    return counts


def user_public_choices_count(user: User) -> int:
    return (
        db.session.query(func.count(VoterChoice.id))
        .join(VoterLog, VoterChoice.voter_log_id == VoterLog.id)
        .join(Poll, VoterLog.poll_id == Poll.id)
        .filter(VoterLog.user_id == user.id, Poll.anonymity_level == 0)
        .scalar()
        or 0
    )


def can_access_poll(poll: Poll, user: Optional[User], poll_ref: Optional[str] = None) -> bool:
    if poll.is_archived and not can_manage_poll(poll, user):
        return False
    if poll.poll_type == "public":
        return True
    if can_manage_poll(poll, user):
        return True
    return bool(poll_ref and poll_ref == poll.unique_code)


def can_manage_poll(poll: Poll, user: Optional[User]) -> bool:
    return bool(user and (user.is_admin or poll.created_by_id == user.id))


def is_valid_image_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]


def detect_image_extension(uploaded) -> Optional[str]:
    try:
        position = uploaded.stream.tell()
        header = uploaded.stream.read(16)
        uploaded.stream.seek(position)
    except (AttributeError, OSError):
        return None

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    return None


def upload_extension(uploaded) -> Optional[str]:
    original = secure_filename(uploaded.filename or "")
    detected_extension = detect_image_extension(uploaded)
    if "." not in original:
        if detected_extension == "jpeg":
            return "jpg"
        return detected_extension if detected_extension in app.config["ALLOWED_EXTENSIONS"] else None

    extension = original.rsplit(".", 1)[1].lower()
    if extension not in app.config["ALLOWED_EXTENSIONS"]:
        return None
    if detected_extension not in IMAGE_EXTENSION_ALIASES.get(extension, set()):
        return None
    return extension


def storage_backend() -> str:
    explicit = str(app.config.get("UPLOAD_BACKEND") or "").strip().lower()
    if explicit in {"local", "supabase", "s3", "cloudinary"}:
        return explicit
    if cloudinary_storage_configured():
        return "cloudinary"
    if supabase_storage_configured():
        return "supabase"
    return "local"


def uses_supabase_storage() -> bool:
    return storage_backend() in {"supabase", "s3"}


def uses_cloudinary_storage() -> bool:
    return storage_backend() == "cloudinary"


def supabase_storage_configured() -> bool:
    endpoint = (app.config.get("SUPABASE_STORAGE_ENDPOINT") or "").strip()
    access_key_id = (app.config.get("SUPABASE_STORAGE_ACCESS_KEY_ID") or "").strip()
    secret_access_key = (app.config.get("SUPABASE_STORAGE_SECRET_ACCESS_KEY") or "").strip()
    return bool(endpoint and access_key_id and secret_access_key)


def cloudinary_storage_configured() -> bool:
    if (app.config.get("CLOUDINARY_URL") or "").strip():
        return True
    cloud_name = (app.config.get("CLOUDINARY_CLOUD_NAME") or "").strip()
    api_key = (app.config.get("CLOUDINARY_API_KEY") or "").strip()
    api_secret = (app.config.get("CLOUDINARY_API_SECRET") or "").strip()
    return bool(cloud_name and api_key and api_secret)


def supabase_storage_public_base_url() -> Optional[str]:
    explicit = (app.config.get("SUPABASE_STORAGE_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit

    endpoint = (app.config.get("SUPABASE_STORAGE_ENDPOINT") or "").strip().rstrip("/")
    bucket = (app.config.get("SUPABASE_STORAGE_BUCKET") or "uploads").strip()
    if not endpoint:
        return None
    public_suffix = f"/storage/v1/object/public/{quote(bucket, safe='')}"
    if endpoint.endswith("/storage/v1/s3"):
        if ".storage.supabase.co/storage/v1/s3" in endpoint:
            return endpoint.replace(".storage.supabase.co/storage/v1/s3", f".supabase.co{public_suffix}")
        return endpoint[: -len("/s3")] + public_suffix
    return endpoint


def supabase_storage_client():
    endpoint = (app.config.get("SUPABASE_STORAGE_ENDPOINT") or "").strip().rstrip("/")
    access_key_id = (app.config.get("SUPABASE_STORAGE_ACCESS_KEY_ID") or "").strip()
    secret_access_key = (app.config.get("SUPABASE_STORAGE_SECRET_ACCESS_KEY") or "").strip()
    if not endpoint or not access_key_id or not secret_access_key:
        return None

    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError as exc:  # pragma: no cover - dependency issue
        raise RuntimeError("Для Supabase storage нужен пакет boto3.") from exc

    region = (app.config.get("SUPABASE_STORAGE_REGION") or "aws-0-us-east-1").strip()
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def supabase_storage_signed_url(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    client = supabase_storage_client()
    if client is None:
        return None
    bucket = (app.config.get("SUPABASE_STORAGE_BUCKET") or "uploads").strip()
    ttl = int(app.config.get("SUPABASE_STORAGE_URL_TTL_SECONDS") or 604800)
    object_key = str(key).lstrip("/")
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": object_key},
            ExpiresIn=max(60, ttl),
        )
    except Exception:
        return None


def is_cloudinary_ref(value: object) -> bool:
    return isinstance(value, str) and value.startswith(CLOUDINARY_REF_PREFIX)


def cloudinary_public_id_from_ref(value: object) -> Optional[str]:
    if not is_cloudinary_ref(value):
        return None
    public_id = str(value)[len(CLOUDINARY_REF_PREFIX) :].strip().strip("/")
    if not public_id or ".." in public_id:
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-/")
    if any(char not in allowed for char in public_id):
        return None
    return public_id[:220]


def cloudinary_storage_modules():
    try:
        import cloudinary
        import cloudinary.uploader
        from cloudinary.utils import cloudinary_url
    except ImportError as exc:  # pragma: no cover - dependency issue
        raise RuntimeError("Для Cloudinary storage нужен пакет cloudinary.") from exc

    if (app.config.get("CLOUDINARY_URL") or "").strip():
        cloudinary.config(secure=True)
    else:
        cloudinary.config(
            cloud_name=(app.config.get("CLOUDINARY_CLOUD_NAME") or "").strip(),
            api_key=(app.config.get("CLOUDINARY_API_KEY") or "").strip(),
            api_secret=(app.config.get("CLOUDINARY_API_SECRET") or "").strip(),
            secure=True,
        )
    return cloudinary.uploader, cloudinary_url


def cloudinary_storage_url(key: Optional[str]) -> Optional[str]:
    public_id = cloudinary_public_id_from_ref(key)
    if not public_id:
        return None
    try:
        _, cloudinary_url = cloudinary_storage_modules()
        url, _ = cloudinary_url(public_id, secure=True, fetch_format="auto", quality="auto")
        return url
    except Exception:
        return None


def storage_object_url(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    if str(key).startswith(("http://", "https://")):
        return str(key)
    if is_cloudinary_ref(key):
        return cloudinary_storage_url(key)
    if uses_supabase_storage() or (storage_backend() == "cloudinary" and supabase_storage_configured()):
        base_url = supabase_storage_public_base_url()
        object_key = quote(str(key).lstrip("/"), safe="")
        if base_url:
            return f"{base_url}/{object_key}"

        signed_url = supabase_storage_signed_url(key)
        if signed_url:
            return signed_url

        bucket = (app.config.get("SUPABASE_STORAGE_BUCKET") or "uploads").strip()
        endpoint = (app.config.get("SUPABASE_STORAGE_ENDPOINT") or "").strip().rstrip("/")
        if endpoint.endswith("/storage/v1/s3"):
            if ".storage.supabase.co/storage/v1/s3" in endpoint:
                return endpoint.replace(".storage.supabase.co/storage/v1/s3", f".supabase.co/storage/v1/object/public/{quote(bucket, safe='')}/{object_key}")
            return endpoint[: -len("/s3")] + f"/storage/v1/object/public/{quote(bucket, safe='')}/{object_key}"
    return f"/uploads/{key}"


def upload_url(filename: Optional[str]) -> Optional[str]:
    return storage_object_url(filename)


def delete_uploaded_file(filename: Optional[str]) -> None:
    if not filename:
        return
    if is_cloudinary_ref(filename):
        public_id = cloudinary_public_id_from_ref(filename)
        if not public_id:
            return
        try:
            uploader, _ = cloudinary_storage_modules()
            uploader.destroy(public_id, resource_type="image", invalidate=True)
        except Exception:
            pass
        return
    if uses_supabase_storage() or (storage_backend() == "cloudinary" and supabase_storage_configured()):
        client = supabase_storage_client()
        if client is None:
            return
        bucket = (app.config.get("SUPABASE_STORAGE_BUCKET") or "uploads").strip()
        try:
            client.delete_object(Bucket=bucket, Key=str(filename).lstrip("/"))
        except Exception:
            pass
        return

    upload_root = Path(app.config["UPLOAD_FOLDER"]).resolve()
    file_path = (upload_root / secure_filename(str(filename))).resolve()
    try:
        if file_path.is_file() and upload_root in file_path.parents:
            file_path.unlink()
    except OSError:
        pass


def user_avatar_url(user: User) -> Optional[str]:
    return upload_url(user.profile_image) or user.yandex_avatar_url


def uploaded_filename(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    raw_value = value.strip()
    public_id = cloudinary_public_id_from_ref(raw_value)
    if public_id:
        return f"{CLOUDINARY_REF_PREFIX}{public_id}"
    filename = secure_filename(raw_value)
    return filename or None


def uploaded_filename_list(value: object) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        items = []
    filenames: list[str] = []
    for item in items:
        filename = uploaded_filename(item)
        if filename and filename not in filenames:
            filenames.append(filename)
        if len(filenames) >= MAX_IMAGES_PER_FIELD:
            break
    return filenames


def dump_image_filenames(filenames: list[str]) -> Optional[str]:
    return json.dumps(filenames[:MAX_IMAGES_PER_FIELD], ensure_ascii=False) if filenames else None


def load_image_filenames(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        raw = json.loads(value)
    except (TypeError, ValueError):
        return []
    return uploaded_filename_list(raw)


def image_url_list(value: Optional[str], fallback_filename: Optional[str] = None, fallback_external: Optional[str] = None) -> list[str]:
    urls: list[str] = []
    for filename in load_image_filenames(value):
        url = upload_url(filename)
        if url and url not in urls:
            urls.append(url)
    fallback_url = upload_url(fallback_filename)
    if fallback_url and fallback_url not in urls:
        urls.append(fallback_url)
    if fallback_external and fallback_external not in urls:
        urls.append(fallback_external)
    return urls[:MAX_IMAGES_PER_FIELD]


def json_to_urlsafe_payload(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def frontend_auth_redirect(payload: Optional[dict[str, object]] = None, error: Optional[str] = None):
    if payload is not None:
        return redirect(f"/#yandex_auth={json_to_urlsafe_payload(payload)}")
    return redirect(f"/#auth_error={quote(error or 'Не удалось выполнить вход через Яндекс.')}")


def frontend_vk_auth_redirect(error: Optional[str] = None):
    if error:
        return redirect(f"/#auth_error={quote(error)}")

    code = request.args.get("code", "").strip()
    device_id = request.args.get("device_id", "").strip()
    if not code or not device_id:
        return redirect(f"/#auth_error={quote('VK ID не вернул код авторизации.')}")

    payload = {"code": code, "device_id": device_id}
    for key in ("state", "expires_in", "ext_id", "type"):
        value = request.args.get(key, "").strip()
        if value:
            payload[key] = value

    return redirect(f"/#vk_auth={json_to_urlsafe_payload(payload)}")


def is_vk_oauth_callback() -> bool:
    return bool(request.args.get("device_id"))


def normalize_username(value: str) -> str:
    cleaned = "".join(char for char in value.strip().lower() if char.isalnum() or char in "._-")
    cleaned = cleaned.strip("._-")[:64]
    if len(cleaned) < 3:
        cleaned = f"{cleaned or 'ya'}_user"
    return cleaned


def normalize_oauth_scope(value: object) -> str:
    if not isinstance(value, str):
        return ""
    scopes = [scope for scope in value.replace(",", " ").split() if scope]
    return " ".join(dict.fromkeys(scopes))


def unique_username(base: str, existing_user: Optional[User] = None) -> str:
    normalized = normalize_username(base)
    candidate = normalized[:80]
    for index in range(1000):
        user = User.query.filter_by(username=candidate).first()
        if user is None or (existing_user is not None and user.id == existing_user.id):
            return candidate
        suffix = f"_{index + 1}"
        candidate = f"{normalized[:80 - len(suffix)]}{suffix}"
    return f"ya_user_{secrets.token_hex(6)}"


def normalize_yandex_gender(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return {"male": "male", "female": "female"}.get(value.strip().lower())


def normalize_vk_gender(value: object) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    return {
        "1": "female",
        "2": "male",
        "female": "female",
        "male": "male",
        "f": "female",
        "m": "male",
    }.get(normalized)


def parse_vk_birth_date(value: object) -> Optional[date]:
    parsed = parse_date(value)
    if parsed:
        return parsed
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    for date_format in ("%d.%m.%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue
    return None


def build_yandex_avatar_url(info: dict[str, object]) -> Optional[str]:
    avatar_id = info.get("default_avatar_id")
    if not avatar_id or info.get("is_avatar_empty") is True:
        return None
    return f"https://avatars.yandex.net/get-yapic/{avatar_id}/islands-200"


def build_vk_avatar_url(info: dict[str, object]) -> Optional[str]:
    for key in ("avatar", "photo_200", "photo_100", "photo_max", "picture"):
        value = str(info.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    return None


def yandex_json_request(url: str, *, data: Optional[dict[str, str]] = None, headers: Optional[dict[str, str]] = None) -> dict[str, object]:
    body = urlencode(data).encode("utf-8") if data is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(url, data=body, headers=request_headers, method="POST" if body is not None else "GET")
    with urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def yandex_username_from_info(info: dict[str, object], yandex_id: str) -> str:
    login = str(info.get("login") or "").strip()
    if login:
        return login
    email = str(info.get("default_email") or "").strip()
    if email and "@" in email:
        return email.split("@", 1)[0]
    return f"yandex_{yandex_id}"


def sync_yandex_user(info: dict[str, object]) -> User:
    yandex_id = str(info.get("id") or "").strip()
    if not yandex_id:
        raise ValueError("Яндекс не вернул идентификатор пользователя.")

    email = str(info.get("default_email") or "").strip() or None
    user = User.query.filter_by(yandex_id=yandex_id).first()
    if user is None and email:
        user = User.query.filter_by(email=email).first()

    if user is None:
        user = User(username=unique_username(yandex_username_from_info(info, yandex_id)))
        user.set_password(secrets.token_urlsafe(32))
        db.session.add(user)
    else:
        user.username = unique_username(user.username or yandex_username_from_info(info, yandex_id), user)

    birth_date = parse_date(info.get("birthday"))
    if birth_date and date(1900, 1, 1) <= birth_date <= date.today():
        user.birth_date = birth_date
    gender = normalize_yandex_gender(info.get("sex"))
    if gender:
        user.gender = gender

    user.yandex_id = yandex_id
    user.email = email or user.email
    user.first_name = str(info.get("first_name") or "").strip() or user.first_name
    user.last_name = str(info.get("last_name") or "").strip() or user.last_name
    user.yandex_avatar_url = build_yandex_avatar_url(info) or user.yandex_avatar_url
    if user.terms_accepted_at is None:
        user.terms_accepted_at = api_now()
    if user.privacy_accepted_at is None:
        user.privacy_accepted_at = api_now()

    db.session.commit()
    return user


def vk_username_from_info(info: dict[str, object], vk_id: str) -> str:
    for key in ("nickname", "screen_name", "username"):
        value = str(info.get(key) or "").strip()
        if value:
            return value
    first_name = str(info.get("first_name") or "").strip()
    last_name = str(info.get("last_name") or "").strip()
    full_name = "_".join(part for part in (first_name, last_name) if part)
    if full_name:
        return full_name
    email = str(info.get("email") or "").strip()
    if email and "@" in email:
        return email.split("@", 1)[0]
    return f"vk_{vk_id}"


def sync_vk_user(data: dict[str, object]) -> User:
    token_payload = data.get("token") if isinstance(data.get("token"), dict) else {}
    info = data.get("user") if isinstance(data.get("user"), dict) else {}
    if not isinstance(token_payload, dict):
        token_payload = {}
    if not isinstance(info, dict):
        info = {}

    access_token = str(token_payload.get("access_token") or data.get("access_token") or "").strip()
    vk_id = str(info.get("user_id") or info.get("id") or token_payload.get("user_id") or data.get("user_id") or "").strip()
    if not access_token or not vk_id:
        raise ValueError("VK ID не вернул токен или идентификатор пользователя.")

    email = str(info.get("email") or token_payload.get("email") or data.get("email") or "").strip() or None
    user = User.query.filter_by(vk_id=vk_id).first()
    if user is None and email:
        user = User.query.filter_by(email=email).first()

    if user is None:
        user = User(username=unique_username(vk_username_from_info(info, vk_id)))
        user.set_password(secrets.token_urlsafe(32))
        db.session.add(user)
    else:
        user.username = unique_username(user.username or vk_username_from_info(info, vk_id), user)

    birth_date = parse_vk_birth_date(info.get("birthday") or info.get("birthdate") or info.get("bdate"))
    if birth_date and date(1900, 1, 1) <= birth_date <= date.today():
        user.birth_date = birth_date
    gender = normalize_vk_gender(info.get("sex") or info.get("gender"))
    if gender:
        user.gender = gender

    user.vk_id = vk_id
    user.email = email or user.email
    user.first_name = str(info.get("first_name") or "").strip() or user.first_name
    user.last_name = str(info.get("last_name") or "").strip() or user.last_name
    user.yandex_avatar_url = build_vk_avatar_url(info) or user.yandex_avatar_url
    if user.terms_accepted_at is None:
        user.terms_accepted_at = api_now()
    if user.privacy_accepted_at is None:
        user.privacy_accepted_at = api_now()

    db.session.commit()
    return user


def debug_auth_allowed() -> bool:
    if not app.config.get("DEBUG_AUTH_ENABLED"):
        return False
    return (request.remote_addr or "") in {"127.0.0.1", "::1", "localhost"}


def ensure_debug_user(role: str) -> User:
    username = "debug_admin" if role == "admin" else "debug_user"
    user_role = "admin" if role == "admin" else "user"
    user = User.query.filter_by(username=username).first()
    if user is None:
        user = User(username=username, role=user_role)
        user.set_password(secrets.token_urlsafe(24))
        db.session.add(user)
    else:
        user.role = user_role
    if user.terms_accepted_at is None:
        user.terms_accepted_at = api_now()
    if user.privacy_accepted_at is None:
        user.privacy_accepted_at = api_now()
    db.session.commit()
    return user


def save_image_upload(uploaded, prefix: str) -> tuple[Optional[str], Optional[tuple]]:
    if uploaded is None or not uploaded.filename:
        return None, api_json("Выберите файл изображения.", 400)

    extension = upload_extension(uploaded)
    if extension is None:
        return None, api_json("Поддерживаются только корректные изображения PNG, JPG, GIF или WEBP.", 400)

    filename = f"{prefix}_{secrets.token_urlsafe(12)}.{extension}"
    if uses_cloudinary_storage():
        if not cloudinary_storage_configured():
            return None, api_json("Cloudinary не настроен: нужны CLOUDINARY_URL или CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY и CLOUDINARY_API_SECRET.", 500)
        public_id = f"{prefix}_{secrets.token_urlsafe(12)}"
        folder = (app.config.get("CLOUDINARY_FOLDER") or "").strip().strip("/")
        if folder:
            public_id = f"{folder}/{public_id}"
        try:
            uploader, _ = cloudinary_storage_modules()
            uploaded.stream.seek(0)
            result = uploader.upload(
                uploaded.stream,
                public_id=public_id,
                resource_type="image",
                overwrite=True,
                unique_filename=False,
                use_filename=False,
                tags=["evote"],
            )
            stored_public_id = str(result.get("public_id") or public_id).strip()
            return f"{CLOUDINARY_REF_PREFIX}{stored_public_id}", None
        except Exception:
            app.logger.exception("Cloudinary upload failed")
            return None, api_json("Не удалось загрузить изображение в Cloudinary.", 502)
    if uses_supabase_storage():
        client = supabase_storage_client()
        if client is None:
            return None, api_json("Supabase Storage не настроен: нужны endpoint, access key и secret key.", 500)
        try:
            uploaded.stream.seek(0)
            client.upload_fileobj(
                uploaded.stream,
                (app.config.get("SUPABASE_STORAGE_BUCKET") or "uploads").strip(),
                filename,
                ExtraArgs={
                    "ContentType": uploaded.mimetype or f"image/{extension}",
                    "CacheControl": "public, max-age=31536000, immutable",
                },
            )
        except Exception:
            return None, api_json("Не удалось загрузить изображение в Supabase Storage.", 502)
    else:
        upload_root = Path(app.config["UPLOAD_FOLDER"])
        upload_root.mkdir(parents=True, exist_ok=True)
        uploaded.save(upload_root / filename)
    return filename, None


def image_content_type(filename: str) -> str:
    extension = Path(filename).suffix.lower().lstrip(".")
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(extension, "application/octet-stream")


def upload_local_file_to_supabase(source: Path, key: str) -> bool:
    client = supabase_storage_client()
    if client is None:
        return False
    bucket = (app.config.get("SUPABASE_STORAGE_BUCKET") or "uploads").strip()
    with source.open("rb") as handle:
        client.upload_fileobj(
            handle,
            bucket,
            key,
            ExtraArgs={
                "ContentType": image_content_type(source.name),
                "CacheControl": "public, max-age=31536000, immutable",
            },
        )
    return True


def make_captcha() -> dict[str, str]:
    left = secrets.randbelow(8) + 2
    right = secrets.randbelow(8) + 2
    operator = "+" if secrets.randbelow(2) == 0 else "-"
    if operator == "-" and right > left:
        left, right = right, left
    answer = left + right if operator == "+" else left - right
    token = jwt.encode(
        {
            "scope": "captcha",
            "answer": str(answer),
            "iat": api_now(),
            "exp": api_now() + timedelta(minutes=10),
        },
        app.config["SECRET_KEY"],
        algorithm=JWT_ALGORITHM,
    )
    return {"question": f"{left} {operator} {right} = ?", "token": token}


def verify_captcha(token: object, answer: object) -> bool:
    if not token or answer in (None, ""):
        return False
    try:
        payload = jwt.decode(str(token), app.config["SECRET_KEY"], algorithms=[JWT_ALGORITHM])
    except (ExpiredSignatureError, InvalidTokenError):
        return False
    return payload.get("scope") == "captcha" and str(answer).strip() == str(payload.get("answer", "")).strip()


def generate_unique_poll_code() -> str:
    for _ in range(20):
        code = generate_unique_code()
        if Poll.query.filter_by(unique_code=code).first() is None:
            return code
    raise RuntimeError("Could not generate unique poll code.")


def serialize_voter_brief(user: User) -> dict[str, object]:
    return {
        "user_id": user.id,
        "username": user.username,
        "profile_image": user_avatar_url(user),
        "auth_provider": user_auth_provider(user),
        "gender": user.gender,
        "age": age_from_birth_date(user.birth_date),
    }


def user_auth_provider(user: User) -> str:
    if user.vk_id:
        return "vk"
    if user.yandex_id:
        return "yandex"
    return "local"


def poll_vote_summary(poll: Poll, include_voters: bool = False, include_counts: bool = True) -> list[dict[str, object]]:
    total = poll.total_votes
    vote_counts = poll_option_vote_counts(poll)
    voters_by_option: dict[int, list[dict[str, object]]] = {}
    if include_voters:
        for choice in public_choice_records(poll):
            voter = serialize_voter_brief(choice.voter_log.user)
            voters_by_option.setdefault(choice.option_id, []).append(
                {
                    "vote_id": choice.id,
                    **voter,
                    "voted_at": format_datetime(choice.voter_log.voted_at),
                }
            )

    return [
        {
            "id": option.id,
            "text": option.text,
            "image_url": option.image_url,
            "image": upload_url(option.image),
            "images": image_url_list(option.images, option.image, option.image_url),
            "votes_count": vote_counts.get(option.id, 0) if include_counts else 0,
            "percent": round((vote_counts.get(option.id, 0) / total * 100), 1) if include_counts and total else 0,
            "voters": voters_by_option.get(option.id, []) if include_voters else [],
        }
        for option in poll.options
    ]


def serialize_user(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "profile_image": user_avatar_url(user),
        "auth_provider": user_auth_provider(user),
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "hide_activity": bool(user.hide_activity),
        "is_blocked": bool(user.is_blocked),
        "blocked_at": format_datetime(user.blocked_at),
        "terms_accepted": bool(user.terms_accepted_at),
        "privacy_accepted": bool(user.privacy_accepted_at),
        "created_at": format_datetime(user.created_at),
    }


def poll_results_visible(poll: Poll, can_manage: bool, has_voted: bool = False) -> bool:
    if can_manage:
        return True
    if not has_voted:
        return False
    if poll.results_visibility in {"always", "after_end"}:
        return True
    if poll.results_visibility == "manual":
        return bool(poll.results_published)
    return False


def poll_settings_snapshot(poll: Poll) -> dict[str, object]:
    return {
        "title": poll.title,
        "description": poll.description,
        "access_type": poll.poll_type,
        "selection_type": "multiple" if poll.allow_multiple_choices else "single",
        "anonymity_level": poll.anonymity_level,
        "max_votes": poll.max_votes,
        "ends_at": format_datetime(poll.ends_at, utc=False),
        "is_active": poll.is_active,
        "is_archived": poll.is_archived,
        "completed_at": format_datetime(poll.completed_at),
        "archived_at": format_datetime(poll.archived_at),
        "results_visibility": poll.results_visibility,
        "results_published": poll.results_published,
        "options": [option.text for option in poll.options],
    }


AUDIT_FIELD_LABELS = {
    "title": "Название",
    "description": "Описание",
    "access_type": "Доступ",
    "selection_type": "Тип выбора",
    "anonymity_level": "Анонимность",
    "max_votes": "Лимит голосующих",
    "ends_at": "Дата окончания",
    "is_active": "Состояние",
    "is_archived": "Архив",
    "completed_at": "Дата завершения",
    "archived_at": "Дата архивации",
    "results_visibility": "Публикация результатов",
    "results_published": "Результаты опубликованы",
    "options": "Варианты",
}


def audit_changes(before: Optional[dict[str, object]], after: dict[str, object]) -> list[dict[str, object]]:
    if not before:
        return []
    changes: list[dict[str, object]] = []
    for key, label in AUDIT_FIELD_LABELS.items():
        old_value = before.get(key)
        new_value = after.get(key)
        if old_value != new_value:
            changes.append({"field": key, "label": label, "old": old_value, "new": new_value})
    return changes


def audit_value_to_text(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def parse_audit_json(value: object) -> dict[str, object]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def add_audit_snapshot(log: PollAuditLog, snapshot: dict[str, object]) -> None:
    db.session.add(
        PollAuditSnapshot(
            audit_log_id=log.id,
            title=str(snapshot.get("title") or ""),
            description=snapshot.get("description"),
            access_type=str(snapshot.get("access_type") or "public"),
            selection_type=str(snapshot.get("selection_type") or "single"),
            anonymity_level=int(snapshot.get("anonymity_level") or 0),
            max_votes=snapshot.get("max_votes"),
            ends_at=snapshot.get("ends_at"),
            is_active=bool(snapshot.get("is_active")),
            is_archived=bool(snapshot.get("is_archived")),
            completed_at=snapshot.get("completed_at"),
            archived_at=snapshot.get("archived_at"),
            results_visibility=str(snapshot.get("results_visibility") or "after_end"),
            results_published=bool(snapshot.get("results_published")),
        )
    )
    for index, option_text in enumerate(snapshot.get("options") or []):
        db.session.add(
            PollAuditSnapshotOption(
                audit_log_id=log.id,
                position=index,
                text=str(option_text),
            )
        )


def add_audit_details(log: PollAuditLog, details: Optional[dict[str, object]]) -> None:
    if not details:
        return

    for change in details.get("changes") or []:
        if not isinstance(change, dict):
            continue
        db.session.add(
            PollAuditChange(
                audit_log_id=log.id,
                field=str(change.get("field") or ""),
                label=str(change.get("label") or ""),
                old_value=audit_value_to_text(change.get("old")),
                new_value=audit_value_to_text(change.get("new")),
            )
        )

    if log.category != "vote" and "anonymity_level" not in details:
        return

    voter = details.get("voter")
    voter_id = voter.get("id") if isinstance(voter, dict) else None
    db.session.add(
        PollAuditVoteDetail(
            audit_log_id=log.id,
            anonymity_level=int(details.get("anonymity_level") or 0),
            voter_id=int(voter_id) if voter_id else None,
            voter_hidden=bool(details.get("voter_hidden")),
            choice_hidden=bool(details.get("choice_hidden")),
        )
    )
    for option in details.get("options") or []:
        if not isinstance(option, dict):
            continue
        option_id = option.get("id")
        db.session.add(
            PollAuditVoteOption(
                audit_log_id=log.id,
                option_id=int(option_id) if option_id else None,
                option_text=str(option.get("text") or ""),
            )
        )


def audit_snapshot_payload(log: PollAuditLog) -> dict[str, object]:
    snapshot = log.snapshot_record
    if snapshot is None:
        return {}
    return {
        "title": snapshot.title,
        "description": snapshot.description,
        "access_type": snapshot.access_type,
        "selection_type": snapshot.selection_type,
        "anonymity_level": snapshot.anonymity_level,
        "max_votes": snapshot.max_votes,
        "ends_at": snapshot.ends_at,
        "is_active": snapshot.is_active,
        "is_archived": snapshot.is_archived,
        "completed_at": snapshot.completed_at,
        "archived_at": snapshot.archived_at,
        "results_visibility": snapshot.results_visibility,
        "results_published": snapshot.results_published,
        "options": [option.text for option in snapshot.options],
    }


def audit_details_payload(log: PollAuditLog) -> Optional[dict[str, object]]:
    details: dict[str, object] = {}
    if log.vote_detail:
        details["anonymity_level"] = log.vote_detail.anonymity_level
        if log.vote_detail.voter:
            details["voter"] = serialize_user(log.vote_detail.voter)
        if log.vote_detail.voter_hidden:
            details["voter_hidden"] = True
        if log.vote_detail.choice_hidden:
            details["choice_hidden"] = True
        if log.vote_options:
            details["options"] = [
                {"id": option.option_id, "text": option.option_text}
                for option in log.vote_options
            ]

    changes = [
        {
            "field": change.field,
            "label": change.label,
            "old": change.old_value,
            "new": change.new_value,
        }
        for change in log.change_records
    ]
    if changes:
        details["changes"] = changes
    return details or None


def record_poll_audit(
    poll: Poll,
    actor: Optional[User],
    action: str,
    *,
    before: Optional[dict[str, object]] = None,
    details: Optional[dict[str, object]] = None,
    category: str = "change",
) -> None:
    snapshot = poll_settings_snapshot(poll)
    payload = details or {}
    changes = audit_changes(before, snapshot)
    if changes and "changes" not in payload:
        payload = {**payload, "changes": changes}
    log = PollAuditLog(
        poll_id=poll.id,
        actor_id=actor.id if actor else None,
        action=action,
        category=category,
    )
    db.session.add(log)
    db.session.flush()
    add_audit_snapshot(log, snapshot)
    add_audit_details(log, payload or None)


def record_vote_audit(poll: Poll, user: User, selected_ids: list[int]) -> None:
    details: dict[str, object] = {"anonymity_level": poll.anonymity_level}
    if poll.anonymity_level == 0:
        selected = [option for option in poll.options if option.id in selected_ids]
        details["voter"] = serialize_user(user)
        details["options"] = [{"id": option.id, "text": option.text} for option in selected]
    elif poll.anonymity_level == 1:
        details["voter"] = serialize_user(user)
        details["choice_hidden"] = True
    else:
        details["voter_hidden"] = True
        details["choice_hidden"] = True

    record_poll_audit(poll, user if poll.anonymity_level in {0, 1} else None, "vote_cast", details=details, category="vote")


def serialize_comment(comment: PollComment) -> dict[str, object]:
    return {
        "id": comment.id,
        "body": comment.body,
        "created_at": format_datetime(comment.created_at),
        "user": serialize_user(comment.user),
    }


def serialize_audit_log(log: PollAuditLog) -> dict[str, object]:
    return {
        "id": log.id,
        "action": log.action,
        "category": log.category,
        "created_at": format_datetime(log.created_at),
        "actor": serialize_user(log.actor) if log.actor else None,
        "snapshot": audit_snapshot_payload(log),
        "details": audit_details_payload(log),
    }


def viewer_key_for_request(user: Optional[User]) -> str:
    if user:
        return f"user:{user.id}"
    raw = f"{request.remote_addr or ''}|{request.headers.get('User-Agent', '')}"
    digest = hashlib.sha256(f"{app.config['SECRET_KEY']}|{raw}".encode("utf-8")).hexdigest()
    return f"anon:{digest}"


def record_poll_view(poll: Poll, user: Optional[User]) -> None:
    viewer_key = viewer_key_for_request(user)
    existing = PollView.query.filter_by(poll_id=poll.id, viewer_key=viewer_key).first()
    if existing:
        existing.viewed_at = api_now()
        return
    db.session.add(PollView(poll_id=poll.id, user_id=user.id if user else None, viewer_key=viewer_key))


def serialize_poll(poll: Poll, viewer: Optional[User] = None, include_logs: bool = False) -> dict[str, object]:
    has_voted = bool(viewer and user_has_voted(viewer.id, poll.id))
    can_manage = can_manage_poll(poll, viewer)
    results_visible = poll_results_visible(poll, can_manage, has_voted)
    participant_names_visible = bool(results_visible and poll.anonymity_level in {0, 1})

    public_votes = []
    if poll.anonymity_level == 0 and results_visible:
        for choice in public_choice_records(poll):
            public_votes.append(
                {
                    "id": choice.id,
                    "user": choice.voter_log.user.username,
                    **serialize_voter_brief(choice.voter_log.user),
                    "option_id": choice.option_id,
                    "option": choice.option.text,
                    "voted_at": format_datetime(choice.voter_log.voted_at),
                }
            )

    participation_log = []
    if include_logs and participant_names_visible:
        for log in sorted(poll.voter_logs, key=lambda item: item.voted_at, reverse=True):
            participation_log.append(
                {
                    "id": log.id,
                    "user": log.user.username,
                    **serialize_voter_brief(log.user),
                    "voted_at": format_datetime(log.voted_at),
                }
            )

    visible_total_votes = poll.voters_count if results_visible else 0
    visible_choices_count = poll.total_votes if results_visible else 0

    return {
        "id": poll.id,
        "code": poll.unique_code,
        "title": poll.title,
        "description": poll.description,
        "description_image": upload_url(poll.description_image),
        "description_images": image_url_list(poll.description_images, poll.description_image),
        "access_type": poll.poll_type,
        "poll_type": "multiple" if poll.allow_multiple_choices else "single",
        "allow_multiple_choices": poll.allow_multiple_choices,
        "is_anonymous": poll.is_anonymous,
        "anonymity_level": poll.anonymity_level,
        "max_votes": poll.max_votes,
        "is_active": poll.is_active,
        "is_archived": poll.is_archived,
        "has_ended": poll.has_ended,
        "ends_at": format_datetime(poll.ends_at, utc=False),
        "is_infinite": poll.ends_at is None,
        "created_at": format_datetime(poll.created_at),
        "completed_at": format_datetime(poll.completed_at),
        "archived_at": format_datetime(poll.archived_at),
        "results_visibility": poll.results_visibility,
        "results_published": poll.results_published,
        "creator": serialize_user(poll.creator),
        "options": poll_vote_summary(
            poll,
            include_voters=bool(poll.anonymity_level == 0 and results_visible),
            include_counts=results_visible,
        ),
        "total_votes": visible_total_votes,
        "choices_count": visible_choices_count,
        "participants": visible_total_votes,
        "views_count": poll.views_count,
        "comments_count": len(poll.comments),
        "spots_left": max(poll.max_votes - poll.voters_count, 0) if poll.poll_type == "limited" and poll.max_votes else None,
        "has_voted": has_voted,
        "can_vote": bool(viewer and poll.can_vote and not has_voted),
        "can_manage": can_manage,
        "results_visible": results_visible,
        "participant_names_visible": participant_names_visible,
        "public_votes": public_votes,
        "participation_log": participation_log,
        "comments": [serialize_comment(comment) for comment in poll.comments[:50]] if include_logs else [],
        "audit_logs": [serialize_audit_log(log) for log in poll.audit_logs[:50]] if include_logs and can_manage else [],
    }


def validate_selected_option_ids(poll: Poll, selected_ids: list[int]) -> Optional[str]:
    if not selected_ids:
        return "Выберите хотя бы один вариант ответа."
    if not poll.allow_multiple_choices and len(selected_ids) != 1:
        return "В этом опросе можно выбрать только один вариант."

    allowed_option_ids = {option.id for option in poll.options}
    if any(option_id not in allowed_option_ids for option_id in selected_ids):
        return "Выбран недопустимый вариант ответа."
    return None


def record_vote(poll: Poll, user: User, selected_ids: list[int]) -> None:
    voted_at = api_now()
    voter_log = VoterLog(user_id=user.id, poll_id=poll.id, voted_at=voted_at)
    db.session.add(voter_log)
    db.session.flush()

    if poll.anonymity_level == 0:
        for option_id in selected_ids:
            db.session.add(VoterChoice(voter_log_id=voter_log.id, option_id=option_id))
    else:
        ballot = AnonymousBallot(poll_id=poll.id, cast_at=voted_at)
        db.session.add(ballot)
        db.session.flush()
        for option_id in selected_ids:
            db.session.add(AnonymousBallotChoice(ballot_id=ballot.id, option_id=option_id))

    record_vote_audit(poll, user, selected_ids)


def build_poll_from_json(data: dict, creator_id: int) -> tuple[Optional[Poll], list[str]]:
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip() or None
    description_image = uploaded_filename(data.get("description_image"))
    description_images = uploaded_filename_list(data.get("description_images"))
    if not description_images and description_image:
        description_images = [description_image]
    description_image = description_images[0] if description_images else description_image
    access_type = (data.get("access_type") or "public").strip().lower()
    poll_mode = (data.get("poll_type") or "single").strip().lower()
    results_visibility = (data.get("results_visibility") or "after_end").strip().lower()
    anonymity_raw = data.get("anonymity_level")
    if anonymity_raw in (None, ""):
        anonymity_level = 2 if bool(data.get("is_anonymous", False)) else 0
    else:
        try:
            anonymity_level = int(anonymity_raw)
        except (TypeError, ValueError):
            anonymity_level = -1
    is_infinite = bool(data.get("is_infinite"))
    ends_at = None if is_infinite else parse_datetime(data.get("ends_at"))
    options = data.get("options") or []
    errors: list[str] = []
    max_votes = None

    if not title:
        errors.append("Укажите название опроса.")
    if len(title) > 200:
        errors.append("Название опроса должно быть не длиннее 200 символов.")
    if description and len(description) > 5000:
        errors.append("Описание опроса должно быть не длиннее 5000 символов.")
    if isinstance(data.get("description_images"), list) and len(data.get("description_images")) > MAX_IMAGES_PER_FIELD:
        errors.append(f"К описанию можно добавить не больше {MAX_IMAGES_PER_FIELD} изображений.")
    if access_type not in {"public", "link", "limited"}:
        errors.append("Недопустимый тип доступа.")
    if poll_mode not in {"single", "multiple"}:
        errors.append("Недопустимый тип выбора.")
    if results_visibility == "always":
        results_visibility = "after_end"
    if results_visibility not in {"after_end", "manual", "hidden"}:
        errors.append("Недопустимый режим публикации результатов.")
    if anonymity_level not in {0, 1, 2}:
        errors.append("Недопустимый режим анонимности.")
    if not is_infinite and ends_at is None:
        errors.append("Укажите дату окончания опроса.")
    elif ends_at is not None and ends_at <= api_now():
        errors.append("Дата окончания должна быть в будущем.")

    if access_type == "limited":
        try:
            max_votes = int(data.get("max_votes"))
        except (TypeError, ValueError):
            errors.append("Укажите максимальное количество голосов.")
        else:
            if max_votes < 1:
                errors.append("Максимальное количество голосов должно быть больше нуля.")

    cleaned_options: list[dict[str, Optional[str]]] = []
    if not isinstance(options, list):
        errors.append("Список вариантов ответа должен быть массивом.")
    else:
        for option in options:
            if not isinstance(option, dict):
                continue
            text_value = (option.get("text") or "").strip()
            image_url = (option.get("image_url") or "").strip()
            image = uploaded_filename(option.get("image"))
            images = uploaded_filename_list(option.get("images"))
            if not images and image:
                images = [image]
            image = images[0] if images else image
            if not text_value:
                continue
            if len(text_value) > 255:
                errors.append("Текст варианта ответа должен быть не длиннее 255 символов.")
            if len(image_url) > 500:
                errors.append("Ссылка на изображение слишком длинная.")
            if image_url and not is_valid_image_url(image_url):
                errors.append("Ссылка на изображение должна начинаться с http:// или https://.")
            if isinstance(option.get("images"), list) and len(option.get("images")) > MAX_IMAGES_PER_FIELD:
                errors.append(f"К варианту ответа можно добавить не больше {MAX_IMAGES_PER_FIELD} изображений.")
            cleaned_options.append({"text": text_value, "image_url": image_url, "image": image, "images": images})

    if len(cleaned_options) < MIN_OPTIONS:
        errors.append(f"Добавьте минимум {MIN_OPTIONS} варианта ответа.")
    if len(cleaned_options) > MAX_OPTIONS:
        errors.append(f"Можно добавить не больше {MAX_OPTIONS} вариантов ответа.")

    option_names = [option["text"].lower() for option in cleaned_options]
    if len(option_names) != len(set(option_names)):
        errors.append("Варианты ответа не должны повторяться.")

    if errors:
        return None, errors

    poll = Poll(
        title=title,
        description=description,
        description_image=description_image,
        description_images=dump_image_filenames(description_images),
        unique_code=generate_unique_poll_code(),
        poll_type=access_type,
        max_votes=max_votes,
        anonymity_level=anonymity_level,
        allow_multiple_choices=(poll_mode == "multiple"),
        results_visibility=results_visibility,
        results_published=False,
        is_active=True,
        created_by_id=creator_id,
        ends_at=ends_at,
    )
    poll.options = [
        Option(text=option["text"], image_url=option["image_url"] or None, image=option["image"], images=dump_image_filenames(option["images"] or []))
        for option in cleaned_options
    ]
    return poll, []


def poll_query_for_user(user: Optional[User]):
    query = Poll.query
    if user is None:
        return query.filter_by(poll_type="public", is_archived=False)
    if user.is_admin:
        return query
    return query.filter(or_(Poll.created_by_id == user.id, (Poll.poll_type == "public") & (Poll.is_archived.is_(False))))


def action_label(action: str) -> str:
    return {
        "created": "Создание опроса",
        "completed": "Завершение опроса",
        "activated": "Повторная активация",
        "results_settings_updated": "Изменение публикации результатов",
    }.get(action, action)


def visibility_label(value: str) -> str:
    return {
        "always": "после голосования",
        "after_end": "после голосования",
        "manual": "после ручной публикации",
        "hidden": "скрыть от участников",
    }.get(value, value)


def export_rows(poll: Poll) -> list[list[object]]:
    rows: list[list[object]] = [
        ["Раздел", "Поле", "Значение"],
        ["Сводка", "Название", poll.title],
        ["Сводка", "Описание", poll.description or ""],
        ["Сводка", "Создатель", poll.creator.username],
        ["Сводка", "Код", poll.unique_code],
        ["Сводка", "Доступ", poll.poll_type],
        ["Сводка", "Анонимность", poll.anonymity_level],
        ["Сводка", "Публикация результатов", visibility_label(poll.results_visibility)],
        ["Сводка", "Результаты опубликованы", "да" if poll.results_published else "нет"],
        ["Сводка", "Просмотров", poll.views_count],
        ["Сводка", "Участников", poll.voters_count],
        ["Сводка", "Ответов", poll.total_votes],
        ["Сводка", "Окончание", format_datetime(poll.ends_at, utc=False) or ""],
        [],
        ["Варианты", "Вариант", "Голоса", "Процент"],
    ]

    total = poll.total_votes
    vote_counts = poll_option_vote_counts(poll)
    for option in poll.options:
        votes_count = vote_counts.get(option.id, 0)
        percent = round((votes_count / total * 100), 1) if total else 0
        rows.append(["Варианты", option.text, votes_count, f"{percent}%"])

    rows.append([])
    if poll.anonymity_level == 0:
        rows.append(["Голоса", "Пользователь", "Вариант", "Время"])
        for choice in public_choice_records(poll):
            rows.append(["Голоса", choice.voter_log.user.username, choice.option.text, format_datetime(choice.voter_log.voted_at) or ""])
    elif poll.anonymity_level == 1:
        rows.append(["Участники", "Пользователь", "Время"])
        for log in sorted(poll.voter_logs, key=lambda item: item.voted_at, reverse=True):
            rows.append(["Участники", log.user.username, format_datetime(log.voted_at) or ""])
    else:
        rows.append(["Анонимность", "Сведения об участниках и выборе скрыты"])

    rows.append([])
    rows.append(["Комментарии", "Пользователь", "Текст", "Время"])
    for comment in sorted(poll.comments, key=lambda item: item.created_at, reverse=True):
        rows.append(["Комментарии", comment.user.username, comment.body, format_datetime(comment.created_at) or ""])

    rows.append([])
    rows.append(["Аудит", "Действие", "Пользователь", "Время", "Снимок"])
    for log in sorted(poll.audit_logs, key=lambda item: item.created_at, reverse=True):
        snapshot = audit_snapshot_payload(log)
        snapshot_text = "; ".join(
            [
                f"title={snapshot.get('title', '')}",
                f"access={snapshot.get('access_type', '')}",
                f"selection={snapshot.get('selection_type', '')}",
                f"anonymity={snapshot.get('anonymity_level', '')}",
                f"results={snapshot.get('results_visibility', '')}",
                f"published={snapshot.get('results_published', '')}",
                f"options={', '.join(snapshot.get('options', []) or [])}",
            ]
        )
        rows.append([
            "Аудит",
            action_label(log.action),
            log.actor.username if log.actor else "system",
            format_datetime(log.created_at) or "",
            snapshot_text,
        ])
    return rows


def build_csv_export(poll: Poll) -> str:
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=";")
    writer.writerows(export_rows(poll))
    return output.getvalue()


CYRILLIC_TO_LATIN = str.maketrans(
    {
        "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E", "Ж": "Zh", "З": "Z",
        "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N", "О": "O", "П": "P", "Р": "R",
        "С": "S", "Т": "T", "У": "U", "Ф": "F", "Х": "Kh", "Ц": "Ts", "Ч": "Ch", "Ш": "Sh",
        "Щ": "Sch", "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E", "Ю": "Yu", "Я": "Ya",
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
        "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
        "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)


def pdf_escape(value: object) -> str:
    transliterated = str(value).translate(CYRILLIC_TO_LATIN)
    cleaned = transliterated.encode("latin-1", "replace").decode("latin-1")
    return cleaned.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf_export(poll: Poll) -> bytes:
    lines = [
        f"eVote report: {poll.title}",
        f"Code: {poll.unique_code}",
        f"Creator: {poll.creator.username}",
        f"Access: {poll.poll_type}",
        f"Results visibility: {poll.results_visibility}",
        f"Views: {poll.views_count}",
        f"Participants: {poll.voters_count}",
        f"Answers: {poll.total_votes}",
        "",
        "Options:",
    ]
    total = poll.total_votes
    vote_counts = poll_option_vote_counts(poll)
    for option in poll.options:
        votes_count = vote_counts.get(option.id, 0)
        percent = round((votes_count / total * 100), 1) if total else 0
        lines.append(f"- {option.text}: {votes_count} ({percent}%)")
    lines.extend(["", "Audit snapshots:"])
    for log in sorted(poll.audit_logs, key=lambda item: item.created_at, reverse=True)[:12]:
        lines.append(f"- {format_datetime(log.created_at)} | {action_label(log.action)} | {log.actor.username if log.actor else 'system'}")

    content = ["BT", "/F1 11 Tf", "50 790 Td", "14 TL"]
    for line in lines[:52]:
        content.append(f"({pdf_escape(line)}) Tj")
        content.append("T*")
    content.append("ET")
    stream = "\n".join(content).encode("latin-1", "replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref_at = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def report_target_summary(report: Report) -> dict[str, object]:
    if report.target_type == "poll":
        poll = db.session.get(Poll, report.target_id)
        return {
            "type": "poll",
            "id": report.target_id,
            "code": poll.unique_code if poll else None,
            "title": poll.title if poll else "Опрос удален",
        }
    if report.target_type == "comment":
        comment = db.session.get(PollComment, report.target_id)
        if comment:
            return {
                "type": "comment",
                "id": report.target_id,
                "title": comment.body[:80],
                "poll_id": comment.poll_id,
                "poll_code": comment.poll.unique_code if comment.poll else None,
                "poll_title": comment.poll.title if comment.poll else None,
            }
        return {"type": "comment", "id": report.target_id, "title": "Комментарий удален"}
    target_user = db.session.get(User, report.target_id)
    return {
        "type": "user",
        "id": report.target_id,
        "title": target_user.username if target_user else "Пользователь удален",
        "is_blocked": bool(target_user.is_blocked) if target_user else False,
    }


def serialize_report(report: Report) -> dict[str, object]:
    return {
        "id": report.id,
        "target_type": report.target_type,
        "target_id": report.target_id,
        "target": report_target_summary(report),
        "reason": report.reason,
        "body": report.body,
        "status": report.status,
        "admin_note": report.admin_note,
        "created_at": format_datetime(report.created_at),
        "reviewed_at": format_datetime(report.reviewed_at),
        "reporter": serialize_user(report.reporter),
        "admin": serialize_user(report.admin) if report.admin else None,
    }


def serialize_support_message(message: SupportMessage) -> dict[str, object]:
    return {
        "id": message.id,
        "body": message.body,
        "created_at": format_datetime(message.created_at),
        "sender": serialize_user(message.sender),
    }


def serialize_support_ticket(ticket: SupportTicket, include_messages: bool = True) -> dict[str, object]:
    return {
        "id": ticket.id,
        "subject": ticket.subject,
        "status": ticket.status,
        "created_at": format_datetime(ticket.created_at),
        "updated_at": format_datetime(ticket.updated_at),
        "user": serialize_user(ticket.user),
        "messages": [serialize_support_message(message) for message in ticket.messages] if include_messages else [],
    }


def validate_report_target(target_type: str, target_id: int, user: User) -> Optional[str]:
    if target_type == "poll":
        poll = db.session.get(Poll, target_id)
        if poll is None or not can_access_poll(poll, user, poll.unique_code):
            return "Опрос не найден."
        return None
    if target_type == "comment":
        comment = db.session.get(PollComment, target_id)
        if comment is None or not can_access_poll(comment.poll, user, comment.poll.unique_code):
            return "Комментарий не найден."
        return None
    if target_type == "user":
        if db.session.get(User, target_id) is None:
            return "Пользователь не найден."
        return None
    return "Недопустимый тип жалобы."


@app.get("/api/health")
def api_health():
    return jsonify({"status": "ok", "time": format_datetime(api_now())})


@app.get("/api/captcha")
def api_captcha():
    return jsonify({"captcha": make_captcha()})


@app.get("/api/auth/config")
def api_auth_config():
    return jsonify(
        {
            "vk_client_id": app.config.get("VK_CLIENT_ID"),
            "vk_redirect_uri": app.config.get("VK_REDIRECT_URI"),
            "vk_sdk_url": app.config.get("VK_SDK_URL"),
        }
    )


@app.get("/api/me")
@require_api_auth
def api_me():
    user = request.api_user  # type: ignore[attr-defined]
    payload = get_api_payload() or {}
    return jsonify({"user": serialize_user(user), "csrf_token": payload.get("csrf")})


@app.post("/api/me/avatar")
@require_write_auth
def api_update_avatar():
    user = request.api_user  # type: ignore[attr-defined]
    uploaded = request.files.get("avatar")
    if uploaded is None or not uploaded.filename:
        return api_json("Выберите файл аватара.", 400)

    filename, error = save_image_upload(uploaded, f"avatar_{user.id}")
    if error is not None:
        return error

    old_filename = user.profile_image
    user.profile_image = filename
    db.session.commit()

    delete_uploaded_file(old_filename)

    return jsonify({"user": serialize_user(user)})

@app.post("/api/uploads/poll-image")
@require_write_auth
def api_upload_poll_image():
    user = request.api_user  # type: ignore[attr-defined]
    filename, error = save_image_upload(request.files.get("image"), f"poll_{user.id}")
    if error is not None:
        return error
    return jsonify({"filename": filename, "url": upload_url(filename)})


@app.patch("/api/me/privacy")
@require_write_auth
def api_update_privacy():
    user = request.api_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}
    user.hide_activity = bool(data.get("hide_activity"))
    db.session.commit()
    return jsonify({"user": serialize_user(user)})


@app.patch("/api/me/details")
@require_write_auth
def api_update_profile_details():
    return api_json("Личные данные профиля недоступны для ручного изменения.", 403)


@app.patch("/api/me/username")
@require_write_auth
def api_update_username():
    user = request.api_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}
    errors: list[str] = []
    username = (data.get("username") or "").strip()

    if len(username) < 3:
        errors.append("Никнейм должен быть не короче 3 символов.")
    if len(username) > 80:
        errors.append("Никнейм должен быть не длиннее 80 символов.")

    if errors:
        return api_json("Ошибка валидации.", 400, details=errors)

    existing = User.query.filter(func.lower(User.username) == username.lower(), User.id != user.id).first()
    if existing:
        return api_json("Пользователь с таким никнеймом уже существует.", 409)

    user.username = username
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return api_json("Пользователь с таким никнеймом уже существует.", 409)

    token, csrf_token = make_jwt_token(user)
    return jsonify({"token": token, "csrf_token": csrf_token, "user": serialize_user(user)})


@app.post("/api/auth/register")
@require_rate_limit("auth")
def api_register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    terms_accepted = bool(data.get("terms_accepted"))
    privacy_accepted = bool(data.get("privacy_accepted"))

    if len(username) < 3:
        return api_json("Имя пользователя должно быть не короче 3 символов.", 400)
    if len(username) > 80:
        return api_json("Имя пользователя должно быть не длиннее 80 символов.", 400)
    if len(password) < 6:
        return api_json("Пароль должен быть не короче 6 символов.", 400)
    if not terms_accepted or not privacy_accepted:
        return api_json("Необходимо принять правила сервиса и согласие на обработку персональных данных.", 400)
    if not verify_captcha(data.get("captcha_token"), data.get("captcha_answer")):
        return api_json("Неверный ответ капчи.", 400)
    if User.query.filter_by(username=username).first():
        return api_json("Пользователь с таким именем уже существует.", 409)

    user = User(username=username)
    user.set_password(password)
    user.terms_accepted_at = api_now()
    user.privacy_accepted_at = api_now()
    db.session.add(user)
    db.session.commit()

    token, csrf_token = make_jwt_token(user)
    return jsonify({"token": token, "csrf_token": csrf_token, "user": serialize_user(user)}), 201


@app.post("/api/auth/login")
@require_rate_limit("auth")
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = User.query.filter_by(username=username).first()

    if user is None or not user.check_password(password):
        return api_json("Неверное имя пользователя или пароль.", 401)

    token, csrf_token = make_jwt_token(user)
    return jsonify({"token": token, "csrf_token": csrf_token, "user": serialize_user(user)})


@app.post("/api/debug/login")
@require_rate_limit("auth")
def api_debug_login():
    if not debug_auth_allowed():
        return api_json("Debug-вход отключен.", 404)

    data = request.get_json(silent=True) or {}
    role = (data.get("role") or "").strip().lower()
    if role not in {"user", "admin"}:
        return api_json("Выберите роль user или admin.", 400)

    user = ensure_debug_user(role)
    token, csrf_token = make_jwt_token(user)
    return jsonify({"token": token, "csrf_token": csrf_token, "user": serialize_user(user)})


@app.post("/api/auth/vk")
@require_rate_limit("auth")
def api_auth_vk():
    if not app.config.get("VK_CLIENT_ID"):
        return api_json("Авторизация через VK ID не настроена.", 400)

    data = request.get_json(silent=True) or {}
    try:
        user = sync_vk_user(data)
    except (ValueError, IntegrityError):
        db.session.rollback()
        app.logger.exception("VK ID auth failed")
        return api_json("Не удалось выполнить вход через VK ID.", 401)

    token, csrf_token = make_jwt_token(user)
    return jsonify({"token": token, "csrf_token": csrf_token, "user": serialize_user(user)})


@app.get("/auth/yandex")
@require_rate_limit("auth")
def auth_yandex():
    if not app.config.get("YANDEX_CLIENT_ID") or not app.config.get("YANDEX_CLIENT_SECRET"):
        return frontend_auth_redirect(error="Авторизация через Яндекс не настроена.")

    state = secrets.token_urlsafe(24)
    session["yandex_oauth_state"] = state
    params = {
        "response_type": "code",
        "client_id": app.config["YANDEX_CLIENT_ID"],
        "redirect_uri": app.config["YANDEX_REDIRECT_URI"],
        "state": state,
    }
    scope = normalize_oauth_scope(app.config.get("YANDEX_SCOPE"))
    if scope:
        params["scope"] = scope
    return redirect(f"{YANDEX_AUTHORIZE_URL}?{urlencode(params)}")


@app.get("/auth/callback")
@require_rate_limit("auth")
def auth_yandex_callback():
    if request.args.get("error"):
        return frontend_auth_redirect(error=request.args.get("error_description") or request.args.get("error"))
    if is_vk_oauth_callback():
        return frontend_vk_auth_redirect()

    expected_state = session.pop("yandex_oauth_state", None)
    if not expected_state or request.args.get("state") != expected_state:
        return frontend_auth_redirect(error="Не удалось подтвердить состояние OAuth-сессии.")

    code = request.args.get("code")
    if not code:
        return frontend_auth_redirect(error="Яндекс не вернул код авторизации.")

    try:
        token_payload = yandex_json_request(
            YANDEX_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": app.config["YANDEX_CLIENT_ID"],
                "client_secret": app.config["YANDEX_CLIENT_SECRET"],
                "redirect_uri": app.config["YANDEX_REDIRECT_URI"],
            },
        )
        access_token = str(token_payload.get("access_token") or "")
        if not access_token:
            return frontend_auth_redirect(error="Яндекс не вернул токен доступа.")

        profile = yandex_json_request(
            f"{YANDEX_USER_INFO_URL}?format=json",
            headers={"Authorization": f"OAuth {access_token}"},
        )
        user = sync_yandex_user(profile)
        token, csrf_token = make_jwt_token(user)
        return frontend_auth_redirect({"token": token, "csrf_token": csrf_token, "user": serialize_user(user)})
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
        app.logger.exception("Yandex OAuth failed")
        return frontend_auth_redirect(error="Не удалось выполнить вход через Яндекс.")


@app.get("/auth/vk/callback")
@require_rate_limit("auth")
def auth_vk_callback():
    if request.args.get("error"):
        return frontend_vk_auth_redirect(error=request.args.get("error_description") or request.args.get("error"))
    return frontend_vk_auth_redirect()


@app.get("/api/polls")
def api_list_polls():
    user = get_api_user()
    polls = poll_query_for_user(user).order_by(Poll.created_at.desc()).all()
    return jsonify({"polls": [serialize_poll(poll, user) for poll in polls]})


@app.get("/api/polls/<poll_ref>")
def api_get_poll(poll_ref: str):
    poll = get_poll_by_ref_or_404(poll_ref)
    user = get_api_user()
    if not can_access_poll(poll, user, poll_ref):
        return api_json("Опрос не найден.", 404)
    try:
        record_poll_view(poll, user)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
    return jsonify({"poll": serialize_poll(poll, user, include_logs=True)})


@app.post("/api/polls")
@require_write_auth
def api_create_poll():
    user = request.api_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}
    poll, errors = build_poll_from_json(data, user.id)
    if errors:
        return api_json("Ошибка валидации.", 400, details=errors)

    db.session.add(poll)
    db.session.flush()
    record_poll_audit(poll, user, "created")
    db.session.commit()
    return jsonify({"poll": serialize_poll(poll, user, include_logs=True)}), 201


@app.post("/api/polls/<poll_ref>/vote")
@require_write_auth
@require_rate_limit("vote")
def api_vote(poll_ref: str):
    user = request.api_user  # type: ignore[attr-defined]
    poll = get_poll_by_ref_or_404(poll_ref)
    if not can_access_poll(poll, user, poll_ref):
        return api_json("Опрос не найден.", 404)
    if not poll.can_vote:
        return api_json("Голосование закрыто.", 400)
    if user_has_voted(user.id, poll.id):
        return api_json("Вы уже голосовали в этом опросе.", 409)

    data = request.get_json(silent=True) or {}
    raw_selected = data.get("option_ids") or []
    if not isinstance(raw_selected, list):
        return api_json("option_ids должен быть массивом.", 400)

    try:
        selected_ids = list(dict.fromkeys(int(value) for value in raw_selected))
    except (TypeError, ValueError):
        return api_json("Некорректный идентификатор варианта.", 400)

    selection_error = validate_selected_option_ids(poll, selected_ids)
    if selection_error:
        return api_json(selection_error, 400)

    try:
        record_vote(poll, user, selected_ids)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return api_json("Вы уже голосовали в этом опросе.", 409)
    except ValueError:
        db.session.rollback()
        return api_json("Некорректный голос.", 400)

    return jsonify({"poll": serialize_poll(poll, user, include_logs=True)})


@app.post("/api/polls/<poll_ref>/complete")
@require_write_auth
def api_complete_poll(poll_ref: str):
    user = request.api_user  # type: ignore[attr-defined]
    poll = get_poll_by_ref_or_404(poll_ref)
    if not can_manage_poll(poll, user):
        return api_json("Недостаточно прав.", 403)
    before = poll_settings_snapshot(poll)
    poll.is_active = False
    poll.completed_at = api_now()
    record_poll_audit(poll, user, "completed", before=before)
    db.session.commit()
    return jsonify({"poll": serialize_poll(poll, user, include_logs=True)})


@app.post("/api/polls/<poll_ref>/activate")
@require_write_auth
def api_activate_poll(poll_ref: str):
    user = request.api_user  # type: ignore[attr-defined]
    poll = get_poll_by_ref_or_404(poll_ref)
    if not can_manage_poll(poll, user):
        return api_json("Недостаточно прав.", 403)
    before = poll_settings_snapshot(poll)
    poll.is_active = True
    poll.completed_at = None
    poll.is_archived = False
    poll.archived_at = None
    record_poll_audit(poll, user, "activated", before=before)
    db.session.commit()
    return jsonify({"poll": serialize_poll(poll, user, include_logs=True)})


@app.post("/api/polls/<poll_ref>/results")
@require_write_auth
def api_update_results_settings(poll_ref: str):
    user = request.api_user  # type: ignore[attr-defined]
    poll = get_poll_by_ref_or_404(poll_ref)
    if not can_manage_poll(poll, user):
        return api_json("Недостаточно прав.", 403)

    data = request.get_json(silent=True) or {}
    visibility = data.get("results_visibility")
    published = data.get("results_published")
    before = poll_settings_snapshot(poll)

    if visibility is not None:
        visibility = str(visibility).strip().lower()
        if visibility == "always":
            visibility = "after_end"
        if visibility not in {"after_end", "manual", "hidden"}:
            return api_json("Недопустимый режим публикации результатов.", 400)
        poll.results_visibility = visibility
    if published is not None:
        poll.results_published = bool(published)
    if poll.results_visibility in {"after_end", "hidden"} and published is None:
        poll.results_published = False

    record_poll_audit(poll, user, "results_settings_updated", before=before)
    db.session.commit()
    return jsonify({"poll": serialize_poll(poll, user, include_logs=True)})


@app.post("/api/polls/<poll_ref>/comments")
@require_write_auth
def api_add_comment(poll_ref: str):
    user = request.api_user  # type: ignore[attr-defined]
    poll = get_poll_by_ref_or_404(poll_ref)
    if not can_access_poll(poll, user, poll_ref):
        return api_json("Опрос не найден.", 404)

    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return api_json("Комментарий не должен быть пустым.", 400)
    if len(body) > 1000:
        return api_json("Комментарий должен быть не длиннее 1000 символов.", 400)

    db.session.add(PollComment(poll_id=poll.id, user_id=user.id, body=body))
    db.session.commit()
    return jsonify({"poll": serialize_poll(poll, user, include_logs=True)}), 201


@app.delete("/api/comments/<int:comment_id>")
@require_write_auth
def api_delete_comment(comment_id: int):
    user = request.api_user  # type: ignore[attr-defined]
    comment = db.session.get(PollComment, comment_id)
    if comment is None:
        return api_json("Комментарий не найден.", 404)
    if not user.is_admin and comment.user_id != user.id:
        return api_json("Недостаточно прав.", 403)
    poll = comment.poll
    db.session.delete(comment)
    db.session.commit()
    return jsonify({"deleted": True, "poll": serialize_poll(poll, user, include_logs=True)})


@app.post("/api/reports")
@require_write_auth
def api_create_report():
    user = request.api_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}
    target_type = (data.get("target_type") or "").strip().lower()
    reason = (data.get("reason") or "").strip()
    body = (data.get("body") or "").strip() or None
    try:
        target_id = int(data.get("target_id"))
    except (TypeError, ValueError):
        return api_json("Некорректная цель жалобы.", 400)

    if not reason:
        return api_json("Укажите причину жалобы.", 400)
    if len(reason) > 80:
        return api_json("Причина жалобы должна быть не длиннее 80 символов.", 400)
    if body and len(body) > 1000:
        return api_json("Описание жалобы должно быть не длиннее 1000 символов.", 400)

    target_error = validate_report_target(target_type, target_id, user)
    if target_error:
        return api_json(target_error, 404 if "не найден" in target_error else 400)

    report = Report(
        reporter_id=user.id,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        body=body,
    )
    db.session.add(report)
    db.session.commit()
    return jsonify({"report": serialize_report(report)}), 201


@app.get("/api/support")
@require_api_auth
def api_support_tickets():
    user = request.api_user  # type: ignore[attr-defined]
    tickets = (
        SupportTicket.query.filter_by(user_id=user.id)
        .order_by(SupportTicket.updated_at.desc(), SupportTicket.created_at.desc())
        .all()
    )
    return jsonify({"tickets": [serialize_support_ticket(ticket) for ticket in tickets]})


@app.post("/api/support")
@require_write_auth
def api_create_support_ticket():
    user = request.api_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}
    subject = (data.get("subject") or "").strip()
    body = (data.get("body") or "").strip()
    if not subject:
        return api_json("Укажите тему обращения.", 400)
    if not body:
        return api_json("Напишите сообщение для поддержки.", 400)
    if len(subject) > 160:
        return api_json("Тема обращения должна быть не длиннее 160 символов.", 400)
    if len(body) > 2000:
        return api_json("Сообщение должно быть не длиннее 2000 символов.", 400)

    ticket = SupportTicket(user_id=user.id, subject=subject, status="open", updated_at=api_now())
    db.session.add(ticket)
    db.session.flush()
    db.session.add(SupportMessage(ticket_id=ticket.id, sender_id=user.id, body=body))
    db.session.commit()
    return jsonify({"ticket": serialize_support_ticket(ticket)}), 201


@app.post("/api/support/<int:ticket_id>/messages")
@require_write_auth
def api_add_support_message(ticket_id: int):
    user = request.api_user  # type: ignore[attr-defined]
    ticket = db.session.get(SupportTicket, ticket_id)
    if ticket is None or (ticket.user_id != user.id and not user.is_admin):
        return api_json("Обращение не найдено.", 404)

    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return api_json("Сообщение не должно быть пустым.", 400)
    if len(body) > 2000:
        return api_json("Сообщение должно быть не длиннее 2000 символов.", 400)

    db.session.add(SupportMessage(ticket_id=ticket.id, sender_id=user.id, body=body))
    ticket.updated_at = api_now()
    if user.is_admin and ticket.status != "closed":
        ticket.status = "answered"
    elif not user.is_admin and ticket.status == "answered":
        ticket.status = "open"
    db.session.commit()
    return jsonify({"ticket": serialize_support_ticket(ticket)}), 201


@app.get("/api/polls/<poll_ref>/export.<export_format>")
@require_api_auth
@require_rate_limit("export")
def api_export_poll(poll_ref: str, export_format: str):
    user = request.api_user  # type: ignore[attr-defined]
    poll = get_poll_by_ref_or_404(poll_ref)
    if not can_manage_poll(poll, user):
        return api_json("Недостаточно прав.", 403)

    filename = f"poll_{poll.unique_code}.{export_format}"
    if export_format == "csv":
        response = app.response_class(build_csv_export(poll), mimetype="text/csv; charset=utf-8")
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    if export_format == "pdf":
        response = app.response_class(build_pdf_export(poll), mimetype="application/pdf")
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    return api_json("Недопустимый формат экспорта.", 404)


@app.delete("/api/polls/<poll_ref>")
@require_write_auth
def api_delete_poll(poll_ref: str):
    user = request.api_user  # type: ignore[attr-defined]
    poll = get_poll_by_ref_or_404(poll_ref)
    if not can_manage_poll(poll, user):
        return api_json("Недостаточно прав.", 403)
    if user.is_admin and request.args.get("hard") == "1":
        poll_id = poll.id
        db.session.delete(poll)
        db.session.commit()
        return jsonify({"deleted": True, "hard": True, "poll_id": poll_id})
    before = poll_settings_snapshot(poll)
    poll.is_archived = True
    poll.is_active = False
    poll.archived_at = api_now()
    if poll.completed_at is None:
        poll.completed_at = poll.archived_at
    record_poll_audit(poll, user, "archived", before=before)
    db.session.commit()
    return jsonify({"deleted": True, "archived": True, "poll": serialize_poll(poll, user, include_logs=True)})


@app.get("/api/activity")
@require_api_auth
def api_activity():
    user = request.api_user  # type: ignore[attr-defined]
    logs = (
        VoterLog.query.filter_by(user_id=user.id)
        .join(Poll)
        .order_by(VoterLog.voted_at.desc())
        .all()
    )
    visible_logs = [log for log in logs if can_access_poll(log.poll, user) or can_manage_poll(log.poll, user)]
    return jsonify(
        {
            "activity": [
                {
                    "poll": serialize_poll(log.poll, user),
                    "voted_at": format_datetime(log.voted_at),
                }
                for log in visible_logs
            ]
        }
    )


@app.get("/api/users")
@require_api_auth
def api_users():
    user = request.api_user  # type: ignore[attr-defined]
    if not user.is_admin:
        return api_json("Admin role required", 403)
    users = User.query.order_by(User.username.asc()).all()
    return jsonify({"users": [serialize_user(item) for item in users]})


@app.get("/api/admin/reports")
@require_admin_read
def api_admin_reports():
    reports = Report.query.order_by(Report.created_at.desc()).all()
    return jsonify({"reports": [serialize_report(report) for report in reports]})


@app.patch("/api/admin/reports/<int:report_id>")
@require_admin
def api_admin_update_report(report_id: int):
    admin = request.api_user  # type: ignore[attr-defined]
    report = db.session.get(Report, report_id)
    if report is None:
        return api_json("Жалоба не найдена.", 404)

    data = request.get_json(silent=True) or {}
    status = (data.get("status") or report.status).strip().lower()
    admin_note = (data.get("admin_note") or "").strip() or None
    if status not in {"pending", "reviewing", "resolved", "rejected"}:
        return api_json("Недопустимый статус жалобы.", 400)
    if admin_note and len(admin_note) > 1000:
        return api_json("Комментарий администратора должен быть не длиннее 1000 символов.", 400)

    report.status = status
    report.admin_id = admin.id
    report.admin_note = admin_note
    report.reviewed_at = api_now()
    db.session.commit()
    return jsonify({"report": serialize_report(report)})


@app.get("/api/admin/support")
@require_admin_read
def api_admin_support_tickets():
    tickets = SupportTicket.query.order_by(SupportTicket.updated_at.desc(), SupportTicket.created_at.desc()).all()
    return jsonify({"tickets": [serialize_support_ticket(ticket) for ticket in tickets]})


@app.patch("/api/admin/support/<int:ticket_id>")
@require_admin
def api_admin_update_support_ticket(ticket_id: int):
    ticket = db.session.get(SupportTicket, ticket_id)
    if ticket is None:
        return api_json("Обращение не найдено.", 404)

    data = request.get_json(silent=True) or {}
    status = (data.get("status") or ticket.status).strip().lower()
    if status not in {"open", "answered", "closed"}:
        return api_json("Недопустимый статус обращения.", 400)
    ticket.status = status
    ticket.updated_at = api_now()
    db.session.commit()
    return jsonify({"ticket": serialize_support_ticket(ticket)})


@app.get("/api/users/<int:user_id>/profile")
def api_user_profile(user_id: int):
    viewer = get_api_user()
    user = db.session.get(User, user_id)
    if user is None:
        return api_json("Пользователь не найден.", 404)

    created_polls = Poll.query.filter_by(created_by_id=user.id).order_by(Poll.created_at.desc()).all()
    visible_polls = [poll for poll in created_polls if can_access_poll(poll, viewer) or can_manage_poll(poll, viewer)]
    can_view_private_profile = bool(viewer and (viewer.is_admin or viewer.id == user.id))
    activity_visible = can_view_private_profile or not user.hide_activity
    participation_logs = (
        VoterLog.query.filter_by(user_id=user.id)
        .join(Poll)
        .order_by(VoterLog.voted_at.desc())
        .all()
        if activity_visible
        else []
    )
    visible_participation_logs = [
        log
        for log in participation_logs
        if can_access_poll(log.poll, viewer) or can_manage_poll(log.poll, viewer)
    ]
    participated_polls = []
    seen_poll_ids: set[int] = set()
    for log in visible_participation_logs:
        if log.poll_id in seen_poll_ids:
            continue
        seen_poll_ids.add(log.poll_id)
        participated_polls.append(log.poll)
    participation_count = VoterLog.query.filter_by(user_id=user.id).count() if activity_visible else 0
    public_votes_count = user_public_choices_count(user) if activity_visible else 0

    return jsonify(
        {
            "profile": {
                "user": serialize_user(user),
                "created_count": len(created_polls),
                "visible_created_count": len(visible_polls),
                "public_votes_count": public_votes_count,
                "participation_count": participation_count,
                "activity_hidden": not activity_visible,
                "created_polls": [serialize_poll(poll, viewer) for poll in visible_polls[:8]],
                "participated_polls": [serialize_poll(poll, viewer) for poll in participated_polls[:8]],
                "recent_activity": [
                    {"poll": serialize_poll(log.poll, viewer), "voted_at": format_datetime(log.voted_at)}
                    for log in visible_participation_logs[:12]
                ],
            }
        }
    )


@app.patch("/api/users/<int:user_id>/role")
@require_admin
def api_update_user_role(user_id: int):
    current_user = request.api_user  # type: ignore[attr-defined]
    user = db.session.get(User, user_id)
    if user is None:
        return api_json("Пользователь не найден.", 404)

    data = request.get_json(silent=True) or {}
    role = data.get("role")
    if role not in {"admin", "user"}:
        return api_json("Недопустимая роль.", 400)
    if user.id == current_user.id and role != "admin":
        return api_json("Нельзя снять роль администратора с самого себя.", 400)

    user.role = role
    db.session.commit()
    return jsonify({"user": serialize_user(user)})


@app.patch("/api/users/<int:user_id>/block")
@require_admin
def api_update_user_block(user_id: int):
    current_user = request.api_user  # type: ignore[attr-defined]
    user = db.session.get(User, user_id)
    if user is None:
        return api_json("Пользователь не найден.", 404)
    if user.id == current_user.id:
        return api_json("Нельзя заблокировать самого себя.", 400)

    data = request.get_json(silent=True) or {}
    blocked = bool(data.get("blocked"))
    user.is_blocked = blocked
    user.blocked_at = api_now() if blocked else None
    db.session.commit()
    return jsonify({"user": serialize_user(user)})


@app.route("/uploads/<filename>")
def uploaded_file(filename: str):
    if uses_supabase_storage():
        public_url = storage_object_url(secure_filename(filename))
        if public_url:
            return redirect(public_url)
        abort(404)
    filepath = Path(app.config["UPLOAD_FOLDER"]) / secure_filename(filename)
    upload_root = Path(app.config["UPLOAD_FOLDER"]).resolve()
    if not filepath.is_file() or upload_root not in filepath.resolve().parents:
        abort(404)
    return send_file(filepath)


def enable_rls_for_public_tables(connection, inspector) -> None:
    if db.engine.dialect.name != "postgresql" or not app.config.get("SUPABASE_ENABLE_RLS_ON_UPGRADE"):
        return
    existing_tables = set(inspector.get_table_names(schema="public"))
    for table_name in RLS_TABLES:
        if table_name in existing_tables:
            connection.execute(text(f'ALTER TABLE IF EXISTS public."{table_name}" ENABLE ROW LEVEL SECURITY'))


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_react_app(path: str):
    if path.startswith("api/"):
        abort(404)
    if path.startswith("static/") or path.startswith("uploads/"):
        abort(404)
    return send_from_directory(CLIENT_DIR, "index.html")


@app.cli.command("init-db")
@with_appcontext
def init_db_command():
    db.create_all()
    inspector = inspect(db.engine)
    with db.engine.begin() as connection:
        enable_rls_for_public_tables(connection, inspector)
    click.echo("Таблицы созданы.")


@app.cli.command("upgrade-db")
@with_appcontext
def upgrade_db_command():
    db.create_all()
    inspector = inspect(db.engine)

    poll_columns = {column["name"] for column in inspector.get_columns("polls")}
    option_columns = {column["name"] for column in inspector.get_columns("options")}
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    audit_columns = (
        {column["name"] for column in inspector.get_columns("poll_audit_logs")}
        if inspector.has_table("poll_audit_logs")
        else set()
    )
    voter_choice_columns = (
        {column["name"] for column in inspector.get_columns("voter_choices")}
        if inspector.has_table("voter_choices")
        else set()
    )

    with db.engine.begin() as connection:
        if "created_at" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN created_at TIMESTAMP"))
            connection.execute(text("UPDATE users SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))
        if "profile_image" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN profile_image VARCHAR(255)"))
        if "yandex_id" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN yandex_id VARCHAR(64)"))
        if "vk_id" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN vk_id VARCHAR(64)"))
        if "email" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(255)"))
        if "first_name" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN first_name VARCHAR(120)"))
        if "last_name" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN last_name VARCHAR(120)"))
        if "yandex_avatar_url" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN yandex_avatar_url VARCHAR(500)"))
        if db.engine.dialect.name == "sqlite":
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_yandex_id_unique ON users (yandex_id) WHERE yandex_id IS NOT NULL"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_vk_id_unique ON users (vk_id) WHERE vk_id IS NOT NULL"))
        else:
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_vk_id_unique ON users (vk_id) WHERE vk_id IS NOT NULL"))
        if "hide_activity" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN hide_activity BOOLEAN NOT NULL DEFAULT FALSE"))
        if "is_blocked" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN is_blocked BOOLEAN NOT NULL DEFAULT FALSE"))
        if "blocked_at" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN blocked_at TIMESTAMP"))
        if "birth_date" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN birth_date DATE"))
        if "gender" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN gender VARCHAR(20)"))
        if "city" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN city VARCHAR(120)"))
        if "terms_accepted_at" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN terms_accepted_at TIMESTAMP"))
        if "privacy_accepted_at" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN privacy_accepted_at TIMESTAMP"))
        if "unique_code" not in poll_columns:
            connection.execute(text("ALTER TABLE polls ADD COLUMN unique_code VARCHAR(12)"))
        if "poll_type" not in poll_columns:
            connection.execute(text("ALTER TABLE polls ADD COLUMN poll_type VARCHAR(20) NOT NULL DEFAULT 'public'"))
        if "max_votes" not in poll_columns:
            connection.execute(text("ALTER TABLE polls ADD COLUMN max_votes INTEGER"))
        if "anonymity_level" not in poll_columns:
            connection.execute(text("ALTER TABLE polls ADD COLUMN anonymity_level INTEGER NOT NULL DEFAULT 2"))
            if "is_anonymous" in poll_columns:
                connection.execute(
                    text("UPDATE polls SET anonymity_level = CASE WHEN is_anonymous THEN 2 ELSE 0 END")
                )
        if "description_image" not in poll_columns:
            connection.execute(text("ALTER TABLE polls ADD COLUMN description_image VARCHAR(255)"))
        if "description_images" not in poll_columns:
            connection.execute(text("ALTER TABLE polls ADD COLUMN description_images TEXT"))
        if "allow_multiple_choices" not in poll_columns:
            connection.execute(text("ALTER TABLE polls ADD COLUMN allow_multiple_choices BOOLEAN NOT NULL DEFAULT FALSE"))
        if "is_active" not in poll_columns:
            connection.execute(text("ALTER TABLE polls ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE"))
        if "is_archived" not in poll_columns:
            connection.execute(text("ALTER TABLE polls ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT FALSE"))
        if "ends_at" not in poll_columns:
            connection.execute(text("ALTER TABLE polls ADD COLUMN ends_at TIMESTAMP"))
        if "completed_at" not in poll_columns:
            connection.execute(text("ALTER TABLE polls ADD COLUMN completed_at TIMESTAMP"))
        if "archived_at" not in poll_columns:
            connection.execute(text("ALTER TABLE polls ADD COLUMN archived_at TIMESTAMP"))
        if "results_visibility" not in poll_columns:
            connection.execute(text("ALTER TABLE polls ADD COLUMN results_visibility VARCHAR(20) NOT NULL DEFAULT 'after_end'"))
        if "results_published" not in poll_columns:
            connection.execute(text("ALTER TABLE polls ADD COLUMN results_published BOOLEAN NOT NULL DEFAULT FALSE"))
        if "results_visibility" in poll_columns:
            connection.execute(text("UPDATE polls SET results_visibility = 'after_end' WHERE results_visibility = 'always'"))
        if "image_url" not in option_columns:
            connection.execute(text("ALTER TABLE options ADD COLUMN image_url VARCHAR(500)"))
        if "image" not in option_columns:
            connection.execute(text("ALTER TABLE options ADD COLUMN image VARCHAR(255)"))
        if "images" not in option_columns:
            connection.execute(text("ALTER TABLE options ADD COLUMN images TEXT"))
        if inspector.has_table("poll_audit_logs") and "category" not in audit_columns:
            connection.execute(text("ALTER TABLE poll_audit_logs ADD COLUMN category VARCHAR(20) NOT NULL DEFAULT 'change'"))

        if inspector.has_table("votes"):
            connection.execute(
                text(
                    """
                    INSERT INTO voter_choices (voter_log_id, option_id)
                    SELECT vl.id, v.option_id
                    FROM votes v
                    JOIN voter_logs vl ON vl.user_id = v.user_id AND vl.poll_id = v.poll_id
                    JOIN polls p ON p.id = v.poll_id
                    WHERE p.anonymity_level = 0
                    AND NOT EXISTS (
                        SELECT 1
                        FROM voter_choices vc
                        WHERE vc.voter_log_id = vl.id AND vc.option_id = v.option_id
                    )
                    """
                )
            )

        if inspector.has_table("voter_choices") and "option_id" in voter_choice_columns:
            hidden_choices = connection.execute(
                text(
                    """
                    SELECT vl.poll_id, vc.voter_log_id, vc.option_id, MIN(vl.voted_at) AS cast_at
                    FROM voter_choices vc
                    JOIN voter_logs vl ON vl.id = vc.voter_log_id
                    JOIN polls p ON p.id = vl.poll_id
                    WHERE p.anonymity_level != 0
                    GROUP BY vl.poll_id, vc.voter_log_id, vc.option_id
                    ORDER BY vc.voter_log_id, vc.option_id
                    """
                )
            ).mappings().all()
            migrated_ballot_ids: dict[int, int] = {}
            for row in hidden_choices:
                voter_log_id = int(row["voter_log_id"])
                ballot_id = migrated_ballot_ids.get(voter_log_id)
                if ballot_id is None:
                    result = connection.execute(
                        AnonymousBallot.__table__.insert().values(
                            poll_id=int(row["poll_id"]),
                            cast_at=row["cast_at"] or api_now(),
                        )
                    )
                    ballot_id = int(result.inserted_primary_key[0])
                    migrated_ballot_ids[voter_log_id] = ballot_id
                connection.execute(
                    AnonymousBallotChoice.__table__.insert().values(
                        ballot_id=ballot_id,
                        option_id=int(row["option_id"]),
                    )
                )
            connection.execute(
                text(
                    """
                    DELETE FROM voter_choices
                    WHERE voter_log_id IN (
                        SELECT vl.id
                        FROM voter_logs vl
                        JOIN polls p ON p.id = vl.poll_id
                        WHERE p.anonymity_level != 0
                    )
                    """
                )
            )

    if "votes_count" in option_columns:
        rows = db.session.execute(
            text(
                """
                SELECT
                    o.id AS option_id,
                    o.poll_id AS poll_id,
                    COALESCE(o.votes_count, 0) AS stored_votes,
                    COALESCE(public_counts.count_value, 0) AS public_votes,
                    COALESCE(hidden_counts.count_value, 0) AS hidden_votes
                FROM options o
                LEFT JOIN (
                    SELECT vc.option_id, COUNT(*) AS count_value
                    FROM voter_choices vc
                    JOIN voter_logs vl ON vl.id = vc.voter_log_id
                    GROUP BY vc.option_id
                ) AS public_counts ON public_counts.option_id = o.id
                LEFT JOIN (
                    SELECT abc.option_id, COUNT(*) AS count_value
                    FROM anonymous_ballot_choices abc
                    JOIN anonymous_ballots ab ON ab.id = abc.ballot_id
                    GROUP BY abc.option_id
                ) AS hidden_counts ON hidden_counts.option_id = o.id
                """
            )
        ).mappings().all()
        for row in rows:
            missing_votes = int(row["stored_votes"]) - int(row["public_votes"]) - int(row["hidden_votes"])
            for _ in range(max(missing_votes, 0)):
                ballot = AnonymousBallot(poll_id=int(row["poll_id"]))
                db.session.add(ballot)
                db.session.flush()
                db.session.add(AnonymousBallotChoice(ballot_id=ballot.id, option_id=int(row["option_id"])))
        db.session.commit()

    if inspector.has_table("poll_audit_logs") and ({"snapshot", "details"} & audit_columns):
        snapshot_expr = "snapshot" if "snapshot" in audit_columns else "NULL AS snapshot"
        details_expr = "details" if "details" in audit_columns else "NULL AS details"
        audit_rows = db.session.execute(
            text(f"SELECT id, {snapshot_expr}, {details_expr} FROM poll_audit_logs")
        ).mappings().all()
        for row in audit_rows:
            log = db.session.get(PollAuditLog, row["id"])
            if log is None:
                continue
            snapshot_payload = parse_audit_json(row["snapshot"])
            details_payload = parse_audit_json(row["details"])
            if snapshot_payload and log.snapshot_record is None:
                add_audit_snapshot(log, snapshot_payload)
            if details_payload and not log.change_records and log.vote_detail is None and not log.vote_options:
                add_audit_details(log, details_payload)
        db.session.commit()

    inspector = inspect(db.engine)
    poll_columns = {column["name"] for column in inspector.get_columns("polls")}
    option_columns = {column["name"] for column in inspector.get_columns("options")}
    audit_columns = (
        {column["name"] for column in inspector.get_columns("poll_audit_logs")}
        if inspector.has_table("poll_audit_logs")
        else set()
    )
    with db.engine.begin() as connection:
        if "is_anonymous" in poll_columns:
            connection.execute(text("ALTER TABLE polls DROP COLUMN is_anonymous"))
        if "votes_count" in option_columns:
            connection.execute(text("ALTER TABLE options DROP COLUMN votes_count"))
        if "snapshot" in audit_columns:
            connection.execute(text("ALTER TABLE poll_audit_logs DROP COLUMN snapshot"))
        if "details" in audit_columns:
            connection.execute(text("ALTER TABLE poll_audit_logs DROP COLUMN details"))
        if inspector.has_table("votes"):
            connection.execute(text("DROP TABLE votes"))

    for poll in Poll.query.filter(or_(Poll.unique_code.is_(None), Poll.unique_code == "")).all():
        poll.unique_code = generate_unique_poll_code()
    db.session.commit()

    inspector = inspect(db.engine)
    with db.engine.begin() as connection:
        enable_rls_for_public_tables(connection, inspector)

    click.echo("Схема базы обновлена.")


@app.cli.command("migrate-uploads")
@with_appcontext
def migrate_uploads_command():
    if not uses_supabase_storage():
        click.echo("UPLOAD_BACKEND не настроен на Supabase.")
        return

    upload_root = Path(app.config["UPLOAD_FOLDER"])
    if not upload_root.exists():
        click.echo("Локальная папка uploads не найдена.")
        return

    uploaded = 0
    failed = 0
    for file_path in upload_root.iterdir():
        if not file_path.is_file():
            continue
        try:
            upload_local_file_to_supabase(file_path, file_path.name)
            uploaded += 1
        except Exception:
            failed += 1

    click.echo(f"Загружено файлов: {uploaded}. Ошибок: {failed}.")


@app.cli.command("create-admin")
@click.argument("username")
@click.password_option()
@with_appcontext
def create_admin_command(username: str, password: str):
    user = User.query.filter_by(username=username).first()
    if user is None:
        user = User(username=username, role="admin")
        db.session.add(user)
    else:
        user.role = "admin"
    user.set_password(password)
    db.session.commit()
    click.echo(f"Администратор {username} готов.")


if __name__ == "__main__":
    app.run(debug=True, load_dotenv=False)
