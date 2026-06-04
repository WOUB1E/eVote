# Система электронного голосования

Веб-приложение для создания и проведения публичных, закрытых и анонимных голосований.

## Стек

- Backend: Flask + SQLAlchemy
- Frontend: React SPA, файлы клиента лежат в `static/react/`
- База данных: PostgreSQL через `DATABASE_URL`, локально возможен SQLite
- Безопасность: JWT, CSRF-токен для write-запросов, Argon2 для паролей
- Тестирование: Pytest

## Актуальная структура

```text
.
├── app.py                  # Flask API и отдача React SPA
├── config.py               # Настройки приложения и БД
├── models.py               # SQLAlchemy-модели
├── seed_db.py              # Демонстрационные данные
├── requirements.txt
├── static/react/
│   ├── index.html
│   ├── app.jsx
│   └── styles.css
├── tests/
│   ├── conftest.py
│   ├── test_api.py
│   ├── test_security.py
│   └── test_voting.py
└── archive/
    └── 2026-05-09_react_rebuild/   # Старый Jinja-интерфейс, старые static-файлы, кэши и логи
```

## Запуск

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SECRET_KEY="long-random-secret"
$env:DATABASE_URL="postgresql+psycopg2://postgres:123@localhost:5432/voting_app"
flask --app app init-db
python seed_db.py
flask --app app run --debug
```

React-интерфейс открывается на `http://127.0.0.1:5000/`.

## API

- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/me`
- `GET /api/polls`
- `GET /api/polls/<code>`
- `POST /api/polls`
- `POST /api/polls/<code>/vote`
- `POST /api/polls/<code>/complete`
- `POST /api/polls/<code>/activate`
- `DELETE /api/polls/<code>`
- `GET /api/activity`
- `GET /api/users`
- `PATCH /api/users/<id>/role`

## Проверка

```powershell
python -m pytest -q
```
