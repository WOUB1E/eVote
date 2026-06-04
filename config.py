import os


def _load_env_file() -> None:
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.isfile(env_path):
        return

    with open(env_path, encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_env_file()


def _database_url() -> str:
    """Возвращает адрес БД с дефолтом для локального SQLite."""
    url = os.getenv(
        "DATABASE_URL",
        None,
    )
    
    if url:
        # Если задана явно - использовать её
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql://", 1)
        return url
    
    # По умолчанию используем SQLite для локальной разработки
    db_path = os.path.join(os.path.dirname(__file__), "instance", "voting_app.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return f"sqlite:///{db_path}"


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me-use-a-long-random-secret")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Параметры загрузки файлов
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
    UPLOAD_BACKEND = os.getenv("UPLOAD_BACKEND", "").lower()
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
    CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "")
    CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")
    CLOUDINARY_URL = os.getenv("CLOUDINARY_URL", "")
    CLOUDINARY_FOLDER = os.getenv("CLOUDINARY_FOLDER", "")
    SUPABASE_STORAGE_ENDPOINT = os.getenv("SUPABASE_STORAGE_ENDPOINT", "")
    SUPABASE_STORAGE_ACCESS_KEY_ID = os.getenv(
        "SUPABASE_STORAGE_ACCESS_KEY_ID",
        os.getenv("SUPABASE_STORAGE_ACCESS_KEY", ""),
    )
    SUPABASE_STORAGE_SECRET_ACCESS_KEY = os.getenv(
        "SUPABASE_STORAGE_SECRET_ACCESS_KEY",
        os.getenv("SUPABASE_STORAGE_SECRET_KEY", ""),
    )
    if not SUPABASE_STORAGE_ACCESS_KEY_ID and SUPABASE_STORAGE_SECRET_ACCESS_KEY:
        SUPABASE_STORAGE_ACCESS_KEY_ID = SUPABASE_STORAGE_SECRET_ACCESS_KEY
    if SUPABASE_STORAGE_ACCESS_KEY_ID and not SUPABASE_STORAGE_SECRET_ACCESS_KEY:
        SUPABASE_STORAGE_SECRET_ACCESS_KEY = SUPABASE_STORAGE_ACCESS_KEY_ID
    SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "uploads")
    SUPABASE_STORAGE_REGION = os.getenv("SUPABASE_STORAGE_REGION", "us-east-1")
    SUPABASE_STORAGE_PUBLIC_BASE_URL = os.getenv("SUPABASE_STORAGE_PUBLIC_BASE_URL", "")
    SUPABASE_STORAGE_URL_TTL_SECONDS = int(os.getenv("SUPABASE_STORAGE_URL_TTL_SECONDS", "604800"))
    SUPABASE_ENABLE_RLS_ON_UPGRADE = os.getenv("SUPABASE_ENABLE_RLS_ON_UPGRADE", "1") != "0"
    SECURITY_HEADERS_ENABLED = os.getenv("SECURITY_HEADERS_ENABLED", "1") != "0"
    RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "1") != "0"
    RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    RATE_LIMITS = {
        "auth": int(os.getenv("RATE_LIMIT_AUTH", "120")),
        "write": int(os.getenv("RATE_LIMIT_WRITE", "300")),
        "vote": int(os.getenv("RATE_LIMIT_VOTE", "180")),
        "export": int(os.getenv("RATE_LIMIT_EXPORT", "120")),
    }
    YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID", "")
    YANDEX_CLIENT_SECRET = os.getenv("YANDEX_CLIENT_SECRET", "")
    YANDEX_REDIRECT_URI = os.getenv("YANDEX_REDIRECT_URI", "http://127.0.0.1:5000/auth/callback")
    YANDEX_SCOPE = os.getenv("YANDEX_SCOPE", "login:info login:email login:avatar login:birthday")
    VK_CLIENT_ID = os.getenv("VK_CLIENT_ID", "54609748")
    VK_REDIRECT_URI = os.getenv("VK_REDIRECT_URI", "https://evote-rvdj.onrender.com/auth/callback")
    VK_SDK_URL = os.getenv("VK_SDK_URL", "https://unpkg.com/@vkid/sdk@2.6.1/dist-sdk/umd/index.js")
    DEBUG_AUTH_ENABLED = os.getenv("DEBUG_AUTH_ENABLED", "0") == "1"
    
    # Типы голосований
    POLL_TYPES = {
        "link": "По ссылке",
        "limited": "Ограниченное кол-во голосов",
        "public": "Публичный",
    }
    
    # Уровни анонимности
    ANONYMITY_LEVELS = {
        0: "Полная гласность (показываем голоса и участников)",
        1: "Скрыто кто за что проголосовал (видны участники)",
        2: "Полная анонимность",
    }
