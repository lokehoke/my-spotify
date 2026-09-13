# My Spotify

Музыкальный стриминг-сервис: Django-бекенд, веб-клиент (React), мобильные приложения (Android, iOS).

Целевой масштаб первой версии — тысячи одновременных пользователей.

## Документация

- [ARCHITECTURE.md](ARCHITECTURE.md) — полная архитектура бекенда: хранение и доставка аудио, поиск, модель данных, API, инфраструктура.
- [CONTRIBUTING.md](CONTRIBUTING.md) — как ведём работу: тикеты, ветки, миграции и общий стенд с базой и аудио на одну машину.

## Быстрый старт (dev)

```bash
cp .env.example .env   # заполнить SECRET_KEY
make up
```

Без `make`: `docker compose -f docker-compose.yml -f compose.dev.yml up -d --build`.

Поднимаются: `web` (Django), `celery` и `celery-beat` (фоновые задачи и расписание),
`db` (PostgreSQL 16), `redis-cache` (кэш и троттлинг, allkeys-lru),
`redis-queue` (брокер Celery, noeviction).

Полезное: `make test`, `make lint`, `make logs`, `make superuser`, `make migrate`.

Схема API: `http://localhost:8000/api/v1/schema/`, Swagger UI (только в dev): `/api/v1/docs/`.

## API

Служебные: `GET /healthz` (liveness), `GET /readyz` (PostgreSQL + Redis, сюда ходит балансировщик).

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/api/v1/auth/register` | регистрация: логин + пароль |
| POST | `/api/v1/auth/token` | вход (логин + пароль), опционально с блоком `device` |
| POST | `/api/v1/auth/token/refresh` | ротация refresh-токена |
| POST | `/api/v1/auth/logout` | отзыв одного refresh-токена |
| POST | `/api/v1/auth/logout/all` | выход на всех устройствах |
| GET/PATCH/DELETE | `/api/v1/me` | профиль, обновление, удаление аккаунта |
| GET | `/api/v1/me/export` | выгрузка персональных данных |
| POST | `/api/v1/me/password` | смена пароля (отзывает все сессии) |
| GET | `/api/v1/me/devices`, DELETE `/api/v1/me/devices/{id}` | устройства и их отзыв |
| GET | `/api/v1/me/subscription`, `/api/v1/plans` | подписка и тарифы |

## Отзыв токенов

Два независимых уровня, чтобы отзыв бил ровно туда, куда нужно:

- **цепочка** (claim `cid`) — одна последовательность refresh-токенов от одного входа.
  Гасится выходом на устройстве и детектом кражи; остальные устройства не трогаются.
  Повтор токена в пределах 10 секунд считается гонкой клиента, а не кражей.
- **все токены пользователя** (claim `tv`) — гасятся сменой пароля и «выйти везде».

Access-токен живёт 15 минут и проверяется подписью без обращения к БД, поэтому отзыв
прекращает доступ в пределах этого окна.

## Статус

Готов «немузыкальный» контур: аутентификация (JWT с ротацией refresh, детект
переиспользования токенов, привязка к устройству), профиль, тарифы и подписки,
удаление аккаунта и экспорт данных.
Инфраструктура: Docker Compose, Celery с расписанием, два Redis, CI на GitHub Actions.

Вход — по логину: 3–32 символа латиницы, цифр, `.`, `-`, `_`, регистр не
различается. Почтового контура нет вообще, поэтому забытый пароль сбрасывает
только админ — пока не появится 2FA с recovery-кодами
([#2](https://github.com/lokehoke/my-spotify/issues/2)). Обоснование —
[ARCHITECTURE.md](ARCHITECTURE.md) §5.2.

Известное отклонение от [ARCHITECTURE.md](ARCHITECTURE.md) §7.4: refresh-токен
пока отдаётся и принимается только в теле запроса. Cookie-контур для веба
(httpOnly, `Path=/api/v1/auth/`) появится вместе с веб-клиентом.

Дальше по [ARCHITECTURE.md](ARCHITECTURE.md): каталог, стриминг, поиск.

## Лицензия

[GNU GPL v3](LICENSE)
