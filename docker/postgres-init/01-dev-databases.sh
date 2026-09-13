#!/bin/sh
# По базе на каждого разработчика: свои миграции каждый катает только к себе,
# поэтому чужой makemigrations не ломает соседа. Общее у команды — не схема,
# а аудио в MinIO и дамп каталога.
#
# Запускается только при первой инициализации тома pgdata. Если стенд уже
# поднимался, новую базу заводите руками:
#   docker compose -f compose.shared.yml exec db createdb -U myspotify <имя>

echo "${DEV_DATABASES:-}" | tr ',' '\n' | tr -d ' ' | while read -r db; do
    [ -n "$db" ] || continue
    echo "init: создаю базу $db"
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
        -c "CREATE DATABASE \"$db\" OWNER \"$POSTGRES_USER\";"
done
