#!/usr/bin/env python
"""Создает демонстрационные данные eVote без очистки реальных записей."""

from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO
import json
import random

from PIL import Image, ImageDraw
from werkzeug.datastructures import FileStorage

from app import api_now, app, delete_uploaded_file, load_image_filenames, record_vote, save_image_upload
from models import Option, Poll, PollComment, User, db


RANDOM = random.Random(20260528)
PASSWORD = "password123"
DEMO_TITLE_PREFIX = "[Демо]"


USER_SPECS = [
    ("demo_author01", "female", date(1986, 3, 12), "Красноярск", "vk"),
    ("demo_author02", "male", date(1991, 8, 4), "Новосибирск", "yandex"),
    ("demo_author03", "female", date(1998, 11, 27), "Томск", "vk"),
    ("demo_author04", "male", date(2002, 6, 18), "Кемерово", "yandex"),
    ("demo_author05", "female", date(1979, 2, 9), "Иркутск", "vk"),
    ("demo_author06", "male", date(1988, 12, 1), "Абакан", "yandex"),
    ("demo_author07", "female", date(2006, 5, 20), "Ачинск", "vk"),
    ("demo_author08", "male", date(2008, 7, 15), "Минусинск", "yandex"),
    ("demo_author09", "female", date(1995, 10, 2), "Красноярск", "vk"),
    ("demo_author10", "male", date(1972, 4, 23), "Норильск", "yandex"),
    ("demo_author11", "female", date(1983, 9, 30), "Дивногорск", "vk"),
    ("demo_author12", "male", date(1999, 1, 7), "Железногорск", "yandex"),
    ("demo_author13", "female", date(2004, 12, 16), "Канск", "vk"),
    ("demo_author14", "male", date(1968, 6, 5), "Лесосибирск", "yandex"),
    ("demo_author15", "female", date(1990, 5, 14), "Сосновоборск", "vk"),
]


POLL_SPECS = [
    {
        "category": "Детсад",
        "title": "Конкурс рисунков «Мой любимый сказочный герой»",
        "description": "Выберите рисунок, который лучше всего передает характер героя.",
        "options": ["Лесная фея", "Добрый богатырь", "Космический кот", "Снежная принцесса"],
        "palette": ("#d6f5ff", "#ff8fb3", "#6cc4a1"),
        "image": True,
    },
    {
        "category": "Детсад",
        "title": "Конкурс поделок из природных материалов",
        "description": "Группа выбирает самую аккуратную и выразительную поделку.",
        "options": ["Осенний домик", "Ежик из шишек", "Кораблик мечты"],
        "palette": ("#fff4d6", "#c77832", "#6d9f5b"),
        "image": True,
    },
    {
        "category": "Детсад",
        "title": "Лучший плакат ко Дню семьи",
        "description": "Оцените плакаты по доброте идеи и аккуратности исполнения.",
        "options": ["Семейное дерево", "Наш общий дом", "Теплые ладони"],
        "palette": ("#ffe8ef", "#ff6b6b", "#ffd166"),
        "image": True,
    },
    {
        "category": "Детсад",
        "title": "Выставка аппликаций «Весенний сад»",
        "description": "Выберите аппликацию, которая выглядит самой живой и праздничной.",
        "options": ["Тюльпаны", "Скворечник", "Радуга над садом", "Первые бабочки"],
        "palette": ("#edf7df", "#44bba4", "#f4d35e"),
        "image": True,
    },
    {
        "category": "Детсад",
        "title": "Голосование за оформление группы",
        "description": "Какую тему выбрать для обновления стенда и уголка творчества?",
        "options": ["Морское путешествие", "Лесная тропинка", "Космос", "Город профессий"],
        "palette": ("#dce9ff", "#3a86ff", "#8338ec"),
        "image": True,
    },
    {
        "category": "Школа",
        "title": "Конкурс рефератов по истории",
        "description": "Выберите работу с лучшей аргументацией и оформлением источников.",
        "options": ["Петровские реформы", "Сибирские экспедиции", "История письменности", "Города Древней Руси"],
        "palette": ("#f3ead7", "#8d6e63", "#2b2d42"),
        "image": True,
    },
    {
        "category": "Школа",
        "title": "Лучший реферат по биологии",
        "description": "Оцените ясность объяснения и качество иллюстраций.",
        "options": ["Экосистемы города", "Мир насекомых", "Вода и здоровье"],
        "palette": ("#e8f8e8", "#2d936c", "#88d498"),
        "image": True,
    },
    {
        "category": "Школа",
        "title": "Конкурс исследовательских работ по физике",
        "description": "Какая работа лучше показывает эксперимент и выводы?",
        "options": ["Маятник", "Электрическая цепь", "Оптика", "Давление жидкости"],
        "palette": ("#e5ecff", "#4361ee", "#4cc9f0"),
        "image": True,
    },
    {
        "category": "Школа",
        "title": "Рефераты по литературе: выбор читателей",
        "description": "Выберите реферат, который захотелось прочитать полностью.",
        "options": ["Образ героя", "Тема дороги", "Сатира в рассказах", "Женские характеры"],
        "palette": ("#f7e8ff", "#9d4edd", "#3c096c"),
        "image": True,
    },
    {
        "category": "Соцопрос",
        "title": "Какая городская зона нужнее району?",
        "description": "Помогите выбрать приоритет для общественного обсуждения.",
        "options": ["Сквер", "Спортплощадка", "Детская зона", "Велодорожка"],
        "palette": ("#e8f7f0", "#0ead69", "#3bceac"),
        "image": True,
    },
    {
        "category": "Соцопрос",
        "title": "Удобное время для родительских собраний",
        "description": "Когда большинству удобнее участвовать в общих встречах?",
        "options": ["Будний вечер", "Суббота утром", "Онлайн вечером", "Короткие встречи по группам"],
        "palette": ("#fff0da", "#f77f00", "#003049"),
        "image": True,
    },
    {
        "category": "Соцопрос",
        "title": "Оценка качества школьного питания",
        "description": "Какой аспект питания важнее улучшить в первую очередь?",
        "options": ["Разнообразие", "Температура блюд", "Овощи и фрукты", "Скорость выдачи"],
        "palette": ("#fefae0", "#dda15e", "#606c38"),
        "image": True,
    },
    {
        "category": "Соцопрос",
        "title": "Формат школьных мероприятий",
        "description": "Какой формат кажется наиболее вовлекающим?",
        "options": ["Квест", "Концерт", "Ярмарка", "Проектная неделя"],
        "palette": ("#f0edff", "#7209b7", "#f72585"),
        "image": True,
    },
    {
        "category": "Соцопрос",
        "title": "Какие кружки добавить в расписание?",
        "description": "Выберите направления, которые интересны детям и родителям.",
        "options": ["Робототехника", "Театр", "Шахматы", "Фотостудия", "Английский клуб"],
        "palette": ("#eaf4ff", "#118ab2", "#06d6a0"),
        "image": True,
    },
    {
        "category": "Соцопрос",
        "title": "Транспорт до школы",
        "description": "Какой способ добраться до школы используется чаще всего?",
        "options": ["Пешком", "Автобус", "Автомобиль", "Самокат/велосипед"],
        "palette": ("#eef2f3", "#2f3e46", "#84a98c"),
        "image": True,
    },
    {
        "category": "Соцопрос",
        "title": "Цифровые сервисы для класса",
        "description": "Какой сервис был бы полезнее для коммуникации?",
        "options": ["Расписание", "Объявления", "Домашние задания", "Запись на встречи"],
        "palette": ("#eaf2ff", "#1d4ed8", "#22c55e"),
        "image": True,
    },
    {
        "category": "Разное",
        "title": "Лучший формат выпускного вечера",
        "description": "Выберите сценарий, который кажется самым теплым и запоминающимся.",
        "options": ["Пикник", "Концерт", "Квест", "Кафе"],
        "palette": ("#fff1f2", "#fb7185", "#0f766e"),
        "image": False,
    },
    {
        "category": "Разное",
        "title": "Идея для общего субботника",
        "description": "Какая задача больше всего подойдет для общего дела?",
        "options": ["Посадка цветов", "Покраска лавочек", "Сбор макулатуры", "Чистый двор"],
        "palette": ("#ecfccb", "#65a30d", "#166534"),
        "image": False,
    },
    {
        "category": "Разное",
        "title": "Выбор темы фотоконкурса",
        "description": "Какая тема даст больше интересных работ?",
        "options": ["Мой район", "Животные рядом", "Семейная история", "Спорт в кадре"],
        "palette": ("#f5f3ff", "#7c3aed", "#f59e0b"),
        "image": False,
    },
    {
        "category": "Разное",
        "title": "Подарок для победителей конкурса",
        "description": "Какой небольшой приз будет наиболее уместным?",
        "options": ["Книга", "Набор для творчества", "Сертификат", "Настольная игра"],
        "palette": ("#fff7ed", "#ea580c", "#2563eb"),
        "image": False,
    },
]


COMMENT_TEMPLATES = [
    "Хорошая идея, сразу видно старание участников.",
    "Мне нравится, что варианты получились разными.",
    "Выбор сложный, все работы достойные.",
    "Поддерживаю этот формат, он понятен и удобен.",
    "Интересно будет посмотреть итоговые результаты.",
    "Кажется, это поможет выбрать наиболее полезное направление.",
]


def make_user(username: str, gender: str, birth_date: date, city: str, provider: str, index: int) -> User:
    user = User.query.filter_by(username=username).first()
    if user is None:
        user = User(username=username)
        user.set_password(PASSWORD)
        db.session.add(user)
    user.role = "user"
    user.gender = gender
    user.birth_date = birth_date
    user.city = city
    user.email = f"{username}@example.test"
    user.first_name = username
    user.last_name = "Demo"
    user.terms_accepted_at = user.terms_accepted_at or api_now()
    user.privacy_accepted_at = user.privacy_accepted_at or api_now()
    if provider == "vk":
        user.vk_id = f"demo-vk-{index}"
        user.yandex_id = None
    else:
        user.yandex_id = f"demo-ya-{index}"
        user.vk_id = None
    return user


def cleanup_previous_demo_polls(users: list[User]) -> None:
    user_ids = [user.id for user in users if user.id]
    if not user_ids:
        return
    old_polls = (
        Poll.query
        .filter(Poll.created_by_id.in_(user_ids), Poll.title.like(f"{DEMO_TITLE_PREFIX}%"))
        .all()
    )
    for poll in old_polls:
        image_keys = set(load_image_filenames(poll.description_images))
        if poll.description_image:
            image_keys.add(poll.description_image)
        for option in poll.options:
            image_keys.update(load_image_filenames(option.images))
            if option.image:
                image_keys.add(option.image)
        for image_key in image_keys:
            delete_uploaded_file(image_key)
        db.session.delete(poll)
    db.session.commit()


def generated_image_bytes(title: str, category: str, palette: tuple[str, str, str], variant: int) -> BytesIO:
    width, height = 1280, 720
    image = Image.new("RGB", (width, height), palette[0])
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 145), fill=palette[1])
    draw.rectangle((0, height - 120, width, height), fill=palette[2])
    for index in range(12):
        x = 70 + index * 104
        y = 210 + ((index + variant) % 3) * 78
        radius = 34 + (index % 3) * 16
        draw.ellipse((x, y, x + radius * 2, y + radius * 2), fill=palette[(index + 1) % 3], outline="#ffffff", width=5)
    draw.rounded_rectangle((70, 190, 1210, 535), radius=32, outline="#ffffff", width=5)
    draw.text((90, 42), category.upper(), fill="#ffffff")
    draw.text((92, 245), title[:52], fill="#16201d")
    draw.text((92, 305), f"eVote demo #{variant + 1}", fill="#334155")
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer


def upload_demo_image(title: str, category: str, palette: tuple[str, str, str], prefix: str, variant: int) -> str:
    buffer = generated_image_bytes(title, category, palette, variant)
    uploaded = FileStorage(stream=buffer, filename=f"{prefix}_{variant}.png", content_type="image/png")
    filename, error = save_image_upload(uploaded, prefix)
    if error is not None or not filename:
        raise RuntimeError(f"Не удалось загрузить демо-изображение для '{title}'")
    return filename


def create_poll(index: int, creator: User, spec: dict[str, object]) -> Poll:
    title = f"{DEMO_TITLE_PREFIX} {spec['title']}"
    poll = Poll(
        title=title,
        description=str(spec["description"]),
        poll_type="public",
        anonymity_level=0,
        allow_multiple_choices=index % 5 == 0,
        is_active=True,
        created_by_id=creator.id,
        created_at=api_now() - timedelta(days=20 - index),
        ends_at=api_now() + timedelta(days=6 + (index % 10)),
        results_visibility="after_end",
        results_published=False,
    )
    if spec["image"]:
        image_key = upload_demo_image(title, str(spec["category"]), spec["palette"], f"demo_poll_{index}", index)
        poll.description_image = image_key
        poll.description_images = json.dumps([image_key], ensure_ascii=False)

    poll.options = []
    for option_text in spec["options"]:
        option = Option(text=str(option_text))
        poll.options.append(option)
    db.session.add(poll)
    return poll


def add_votes(poll: Poll, users: list[User]) -> None:
    voters = RANDOM.sample(users, RANDOM.randint(6, min(12, len(users))))
    for voter in voters:
        if poll.allow_multiple_choices:
            options = RANDOM.sample(poll.options, RANDOM.randint(1, min(2, len(poll.options))))
        else:
            options = [RANDOM.choice(poll.options)]
        record_vote(poll, voter, [option.id for option in options])


def add_comments(poll: Poll, users: list[User]) -> None:
    for index in range(RANDOM.randint(2, 5)):
        user = RANDOM.choice(users)
        body = RANDOM.choice(COMMENT_TEMPLATES)
        db.session.add(
            PollComment(
                poll_id=poll.id,
                user_id=user.id,
                body=body,
                created_at=api_now() - timedelta(hours=RANDOM.randint(1, 96), minutes=index * 7),
            )
        )


def seed_database() -> None:
    with app.app_context():
        users = [
            make_user(username, gender, birth_date, city, provider, index)
            for index, (username, gender, birth_date, city, provider) in enumerate(USER_SPECS, start=1)
        ]
        db.session.commit()

        cleanup_previous_demo_polls(users)

        polls: list[Poll] = []
        for index, spec in enumerate(POLL_SPECS, start=1):
            creator = users[(index - 1) % len(users)]
            poll = create_poll(index, creator, spec)
            polls.append(poll)
        db.session.commit()

        for poll in polls:
            add_votes(poll, users)
            add_comments(poll, users)
        db.session.commit()

        print(f"Создано демо-пользователей: {len(users)}")
        print(f"Создано демо-голосований: {len(polls)}")
        print("Пароль демо-пользователей: password123")


if __name__ == "__main__":
    seed_database()
