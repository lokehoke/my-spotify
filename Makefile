COMPOSE = docker compose -f docker-compose.yml -f compose.dev.yml
# Общий стенд живёт отдельным проектом: перезапуск своего web не должен
# ронять базу, в которую смотрит коллега
SHARED = docker compose -f compose.shared.yml

.PHONY: up down logs shell migrate makemigrations test lint fmt superuser rebuild shared-up shared-down shared-logs shared-psql

up:            ## Поднять стек (web, celery, postgres, redis x2)
	$(COMPOSE) up -d

down:          ## Остановить стек
	$(COMPOSE) down

rebuild:       ## Пересобрать образы и поднять
	$(COMPOSE) up -d --build

logs:
	$(COMPOSE) logs -f web celery

shell:
	$(COMPOSE) exec web python manage.py shell

migrate:
	$(COMPOSE) run --rm web python manage.py migrate

makemigrations:
	$(COMPOSE) run --rm web python manage.py makemigrations

superuser:
	$(COMPOSE) run --rm web python manage.py createsuperuser

test:
	$(COMPOSE) run --rm web pytest

lint:
	$(COMPOSE) run --rm web sh -c "ruff format --check . && ruff check ."

fmt:
	$(COMPOSE) run --rm web sh -c "ruff format . && ruff check --fix ."

shared-up:     ## Поднять общий стенд (postgres + minio) — один раз на машине
	$(SHARED) up -d

shared-down:   ## Остановить стенд (данные остаются в томах)
	$(SHARED) down

shared-logs:
	$(SHARED) logs -f db minio

shared-psql:   ## psql в служебную базу стенда
	$(SHARED) exec db psql -U myspotify -d postgres
