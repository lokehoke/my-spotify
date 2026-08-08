# My Spotify

Музыкальный стриминг-сервис: Django-бекенд, веб-клиент (React), мобильные приложения (Android, iOS).

Целевой масштаб первой версии — тысячи одновременных пользователей.

## Документация

- [ARCHITECTURE.md](ARCHITECTURE.md) — полная архитектура бекенда: хранение и доставка аудио, поиск, модель данных, API, инфраструктура.

## Быстрый старт (dev)

```bash
cp .env.example .env   # заполнить SECRET_KEY
make up
```

Без `make`: `docker compose -f docker-compose.yml -f compose.dev.yml up -d --build`.

Поднимаются: `web` (Django), `celery` (фоновые задачи), `db` (PostgreSQL 16),
`redis-cache` (кэш и троттлинг, allkeys-lru), `redis-queue` (брокер Celery, noeviction).

Полезное: `make test`, `make lint`, `make logs`, `make superuser`, `make migrate`.

Схема API: `http://localhost:8000/api/v1/schema/`, Swagger UI (только в dev): `/api/v1/docs/`.

## API

Служебные: `GET /healthz` (liveness), `GET /readyz` (PostgreSQL + Redis, сюда ходит балансировщик).

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/api/v1/auth/register` | регистрация, шлёт письмо верификации |
| POST | `/api/v1/auth/token` | вход (email + пароль), опционально с блоком `device` |
| POST | `/api/v1/auth/token/refresh` | ротация refresh-токена |
| POST | `/api/v1/auth/logout` | отзыв одного refresh-токена |
| POST | `/api/v1/auth/logout/all` | выход на всех устройствах |
| POST | `/api/v1/auth/email/verify/request` · `/confirm` | подтверждение email |
| POST | `/api/v1/auth/password/reset` · `/confirm` | сброс забытого пароля |
| GET/PATCH/DELETE | `/api/v1/me` | профиль, обновление, удаление аккаунта |
| GET | `/api/v1/me/export` | выгрузка персональных данных |
| POST | `/api/v1/me/password` | смена пароля (отзывает все сессии) |
| GET | `/api/v1/me/devices`, DELETE `/api/v1/me/devices/{id}` | устройства и их отзыв |
| GET | `/api/v1/me/subscription`, `/api/v1/plans` | подписка и тарифы |

## Статус

Готов «немузыкальный» контур: аутентификация (JWT с ротацией refresh, детект
переиспользования токенов, привязка к устройству), профиль, тарифы и подписки,
верификация email, сброс пароля, удаление аккаунта и экспорт данных.
Инфраструктура: Docker Compose, Celery, два Redis, CI на GitHub Actions.

Дальше по [ARCHITECTURE.md](ARCHITECTURE.md): каталог, стриминг, поиск.

## Лицензия

[GNU GPL v3](LICENSE)
