# My Spotify

Музыкальный стриминг-сервис: Django-бекенд, веб-клиент (React), мобильные приложения (Android, iOS).

Целевой масштаб первой версии — тысячи одновременных пользователей.

## Документация

- [ARCHITECTURE.md](ARCHITECTURE.md) — полная архитектура бекенда: хранение и доставка аудио, поиск, модель данных, API, инфраструктура.

## Быстрый старт (dev)

```bash
cp .env.example .env   # заполнить SECRET_KEY
docker compose -f docker-compose.yml -f compose.dev.yml up -d --build
```

После старта: `GET http://localhost:8000/healthz`, `/readyz`, `/api/v1/ping`.

Структура бекенда — модульный Django-монолит по [ARCHITECTURE.md](ARCHITECTURE.md) §7.1:
`config/` (настройки base/dev/prod), `apps/core` (healthchecks), `apps/users` (кастомная модель пользователя).

## Статус

Скелет бекенда: Django 5.2 + DRF + PostgreSQL 16 в Docker Compose, ручки healthcheck работают.
Дальше по плану: JWT-авторизация, каталог, стриминг, поиск.

## Лицензия

[GNU GPL v3](LICENSE)
