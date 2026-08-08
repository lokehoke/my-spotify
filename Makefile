COMPOSE = docker compose -f docker-compose.yml -f compose.dev.yml

.PHONY: up down logs shell migrate makemigrations test lint fmt superuser rebuild

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
