# Архитектура музыкального стриминг-сервиса

Целевой продукт — музыкальный стриминг-сервис («аналог Spotify» для тысяч одновременных пользователей): каталог, стриминг с офлайн-режимом на мобильных, плейлисты, лайки, поиск с опечатками и русской спецификой, чарты и простые рекомендации. Клиенты — web (React), Android, iOS; бекенд — Django.

---

## 1. Обзор и ключевые принципы

**1. Модульный Django-монолит.** Один деплоймент, одна кодовая база, один PostgreSQL — но жёсткие внутренние границы между Django-приложениями: однонаправленные зависимости, общение только через `services.py`, запрет межприложенческих сигналов; всё закреплено import-linter в CI. Пиковая нагрузка на API — порядка сотни-полутора RPS (см. «Допущения и оценка нагрузки») — монолит закрывает её с многократным запасом, а границы дают дешёвый путь к выносу модуля в отдельный сервис, если это когда-нибудь понадобится.

**2. Аудио никогда не проксируется через Django.** Бекенд — только control plane: проверил права, подписал URL, ответил за миллисекунды. Аудиобайты идут по пути «клиент → CDN → объектное хранилище» и в мощностях бекенда не участвуют. Это главный трюк архитектуры: благодаря ему «тысячи одновременных слушателей» превращаются в задачу для 2–3 небольших машин.

**3. Один API для всех клиентов.** `/api/v1/...`, JSON, единый контракт в OpenAPI (drf-spectacular), из которого генерируются типизированные SDK для Kotlin, Swift и TypeScript. Никаких отдельных «мобильных» эндпоинтов и никаких серверных сессий — JWT для всех клиентов, включая веб.

**4. PostgreSQL — единственный источник истины.** Redis (кэш, счётчики, брокер Celery), Meilisearch (поисковый индекс) и CDN-кэш — производные или временные данные: любое из этих хранилищ можно потерять и восстановить из PostgreSQL без потери пользовательских данных.

**5. Технологии по масштабу.** В архитектуре нет Kubernetes, микросервисов, Kafka, DRM и адаптивного битрейта — у каждой из этих вещей есть конкретный триггер появления, зафиксированный в «Пути масштабирования». Стек: Django + DRF, PostgreSQL 16, Redis 7 (два инстанса), Celery, Meilisearch, Cloudflare R2 + CDN + Workers, Docker Compose, GitHub Actions.

---

## 2. Допущения и оценка нагрузки

Единая таблица допущений. Все цифры, сметы и размеры машин в документе считаются от неё.

| Параметр | Значение |
|---|---|
| Каталог | **100 тыс. треков** (~20–30 тыс. альбомов, ~10–15 тыс. артистов, тысячи публичных плейлистов) |
| Регистраций всего | ~100 тыс. |
| DAU | **10 тыс.** |
| Пиковая одновременность | **2–3 тыс. слушателей** |
| Среднее прослушивание | 1,5 ч/день на активного пользователя |
| Средний трек | 3,5 мин |
| Битрейт по умолчанию | 160 kbps (AAC-LC), лестница качеств 96/160/320 |

### Производные величины

**Трафик аудио (несёт CDN, не Django).** 160 kbps = 20 КБ/с = 72 МБ/час на слушателя.

| Показатель | Расчёт | Значение |
|---|---|---|
| Суточный трафик CDN | 10 000 DAU × 1,5 ч × 72 МБ/ч | **≈ 1,1 ТБ/день** |
| Месячный трафик CDN | 1,1 ТБ × 30 | **≈ 32 ТБ/мес** |
| Пиковая полоса CDN | 2 500–3 000 слушателей × 160 kbps | **≈ 0,4–0,5 Гбит/с** |
| Хранилище: FLAC-мастера | 100 тыс. × ~30 МБ | ~3 ТБ |
| Хранилище: AAC-транскоды | 100 тыс. × 15,1 МБ (96+160+320) | ~1,5 ТБ |
| Хранилище: обложки | 30–50 тыс. × ~1,2 МБ | ~50 ГБ |
| **Хранилище всего** | | **≈ 4,5–4,6 ТБ** |

0,4–0,5 Гбит/с — серьёзно для одиночного VPS (упрётесь в канал и CPU на TLS), но тривиально для CDN.

**Нагрузка на API (несёт Django).** Слушатель, у которого играет музыка, почти не трогает API:

| Источник запросов | Частота | RPS при 2 500 одновременных |
|---|---|---|
| Смена трека (карточка следующего + `POST /stream`) | каждые ~3,5 мин, 2 запроса | ~25 |
| Батч playback-событий (прогресс/завершения) | раз в 60 с | ~40 |
| Активный браузинг и поиск (~10% пользователей) | 1 запрос / 4 с | ~60 |
| **Итого пик** | | **~120–150 RPS** (закладываем < 300) |

При 1–3 индексных запросах к PostgreSQL на эндпоинт это ~150–450 QPS на БД (до ~900 при заложенном бюджете 300 RPS) — лёгкий режим для PostgreSQL с корректными индексами. Пик на поисковый эндпоинт (search-as-you-type с debounce 250–300 мс; активно ищут единицы процентов онлайна) — **~30–80 RPS**.

**События прослушиваний:** 10 тыс. DAU × ~40 событий/день ≈ 400 тыс. строк/день ≈ **12 млн строк/мес ≈ 1,5–2 ГБ/мес** с индексами.

Вывод: узкое место архитектуры — **не** число слушателей. Всё тяжёлое (аудиобайты) уходит на CDN и объектное хранилище, которые масштабируются сами; бекенд обслуживает контрольный трафик уровня «две небольшие ноды с двукратным запасом». Никакого Kubernetes и микросервисов — это нагрузка для 2–3 машин плюс managed-сервисы.

---

## 3. Общая схема системы

```
                     Клиенты: Web (React) / Android / iOS
                                    │
        ┌───────────────────────────┼────────────────────────────────┐
        │ HTTPS: JSON API           │ HTTPS: аудио (Range)           │ HTTPS: обложки
        ▼                           ▼                                ▼
  Load Balancer              Cloudflare CDN                   Cloudflare CDN
  (health: /readyz)          media.example.com                img.example.com
        │                           │                                │
   ┌────┴────┐              Worker: HMAC/TTL                         │
   ▼         ▼                      │ промах кэша                    │ промах кэша
 app-1     app-2                    ▼                                ▼
 nginx + gunicorn (WSGI)     R2: бакет audio                  R2: бакет images
 Django + DRF                (aac_96/160/320.m4a)             (WebP 64/300/640)
   │  │  │  │                       ▲
   │  │  │  │                       │ публикация транскодов
   │  │  │  └───► Celery-ноды:      │
   │  │  │        services-1: очереди default / search_index / analytics
   │  │  │        media-1:    очередь media (ffmpeg) ◄──── R2: бакет masters
   │  │  │                                                 (original.flac)
   │  │  ├──► PostgreSQL 16 (managed, PITR) — источник истины
   │  │  ├──► Redis-cache (2 ГБ, allkeys-lru) — кэш API, лайк-сеты, rate limiting
   │  │  ├──► Redis-queue (1 ГБ, noeviction) — брокер Celery + буфер счётчиков
   │  │  └──► Meilisearch (services-1) — поисковый индекс, производная от PostgreSQL
```

Роли компонентов:

- **app-1 / app-2** — stateless Django-ноды (nginx + gunicorn с синхронными WSGI-воркерами). Взаимозаменяемы, добавляются за балансировщиком по мере роста.
- **PostgreSQL 16 (managed)** — единственный stateful-компонент, потеря которого фатальна: пользователи, каталог, плейлисты, история. Бэкапы и PITR — см. «Инфраструктура».
- **Redis × 2** — два инстанса с разными политиками вытеснения: кэш (можно терять) и брокер/счётчики (терять нельзя). Детали — в «Модели данных».
- **Meilisearch** — поисковый индекс; восстанавливается полной переиндексацией из PostgreSQL за минуты.
- **Celery** — транскодирование (отдельная нода, CPU-bound), индексация поиска, письма, агрегации, beat-расписание.
- **Cloudflare R2 + CDN + Worker** — хранение и доставка аудио и изображений; Worker валидирует HMAC-подпись ссылок на edge. Django в пути аудио не участвует.

---

## 4. Хранение и доставка аудио

### 4.1. Общая схема

Главный принцип: **Django никогда не отдаёт аудиобайты**. Он проверяет права, подписывает URL и отвечает за миллисекунды; тяжёлый трафик идёт по пути «клиент → CDN → объектное хранилище». При 2–3 тыс. одновременных слушателей на 160 kbps это ~0,4–0,5 Гбит/с постоянного трафика — прокси через gunicorn съел бы все воркеры длинными соединениями, а CDN + R2 этого даже не заметят.

```
Клиент ── POST /api/v1/tracks/{id}/stream {"quality": "normal"} ──► Django
   │        (JWT, подписка, потолок качества тарифа; генерация HMAC-подписи)
   │ ◄── 200 {"url": "https://media.example.com/{audio_uuid}/v1/aac_160.m4a?e=...&t=...", ...}
   │
   └── GET + Range ──► Cloudflare CDN ── Worker: проверка HMAC/TTL ──► R2 (бакет audio)
```

Полный контракт эндпоинта стрима — в разделе «Django-бекенд и API»; здесь — механика хранения, подписи и доставки.

### 4.2. Формат доставки: progressive download, не HLS

**Решение: progressive download — один `.m4a`-файл на каждое качество, доставка по HTTP Range-запросам через CDN.** HLS на этом масштабе — переусложнение.

Сравнение для музыки (не видео):

| Критерий | Progressive (Range) | HLS (сегменты) |
|---|---|---|
| Объектов на трек | 3 (по одному на качество) | ~67 (21 сегмент × 3 битрейта + 4 плейлиста при 10-сек сегментах на 3,5-мин трек) |
| Перемотка | нативно через `Range: bytes=` | через плейлист, тоже работает |
| Веб-плеер | нативный `<audio>` во всех браузерах | нужен hls.js (MSE), Safari нативно |
| Android | Media3/ExoPlayer `ProgressiveMediaSource` | поддерживается |
| iOS | AVPlayer нативно | поддерживается |
| Подписанные URL | 1 подпись на файл | подпись каждого сегмента либо cookie/переписывание плейлистов |
| Адаптивный битрейт | нет (качество выбирается на старте трека) | да |

Единственное реальное преимущество HLS — смена битрейта посреди трека. Для музыки это почти бесполезно: максимальный битрейт 320 kbps тянет даже плохой LTE, а трек длится 3–4 минуты — клиент просто выбирает качество перед стартом (настройка пользователя + тип сети: Wi-Fi → high, mobile → normal). Именно так работает сам Spotify — фиксированное качество без ABR.

**Когда переходить на HLS (fMP4/CMAF):** появление DRM (FairPlay работает только поверх HLS), живые радио-эфиры или контракты с мейджор-лейблами — см. «Путь масштабирования». Миграция несложная: `ffmpeg -f hls` из тех же мастеров, эндпоинт стрима начинает отдавать URL манифеста вместо файла.

Обязательное требование к файлам: `-movflags +faststart` при транскодировании — moov-атом переносится в начало файла, иначе плеер не начнёт воспроизведение, пока не скачает хвост.

### 4.3. Кодеки и битрейты

**Решение: один кодек AAC-LC в контейнере MP4 (`.m4a`), три ступени качества — 96/160/320 kbps. Эта лестница едина для всего документа: пайплайна транскодирования, модели данных, сметы хранилища и потолков тарифов.**

| Качество | Кодек | Битрейт | Байт/сек | Трек 3,5 мин | Назначение |
|---|---|---|---|---|---|
| `low` | AAC-LC | 96 kbps | 12 КБ/с | ~2,5 МБ | мобильная сеть, экономия трафика |
| `normal` | AAC-LC | 160 kbps | 20 КБ/с | ~4,2 МБ | дефолт |
| `high` | AAC-LC | 320 kbps | 40 КБ/с | ~8,4 МБ | Wi-Fi / премиум |

Почему AAC, а не Opus: Opus даёт лучшее качество на битрейт (~64 kbps Opus ≈ 96 kbps AAC) и бесплатен, но поддержка на Apple-платформах до сих пор костыльная (нужен контейнер CAF либо свежие версии Safari, AVPlayer капризничает). Один кодек AAC-LC играет **нативно везде**: HTML5 `<audio>`, ExoPlayer, AVPlayer — ноль клиентского кода на декодирование. Переход на Opus имеет смысл позже как оптимизация трафика для Android/веба (−30–40% трафика) с AAC-фолбэком для iOS — но это удвоение матрицы транскодов, на старте не нужно (см. «Путь масштабирования»).

Энкодер: нативный `aac` из ffmpeg достаточен на битрейтах ≥128 kbps; для `low` 96 kbps желательно собрать ffmpeg с `libfdk_aac` (заметно лучше на низких битрейтах; лицензия не позволяет распространять бинарь, но собрать для себя — можно).

### 4.4. Пайплайн загрузки трека

Загрузка — админская операция (лейблы/контент-менеджеры через админку), не пользовательская, поэтому нагрузка мизерная, но файлы большие (FLAC 25–40 МБ, WAV-мастера крупнее) — они не должны течь через воркеры gunicorn: один аплоад занял бы воркер на минуты. Грузим **мимо Django, напрямую в бакет по presigned PUT**. Это единственный флоу загрузки аудио в системе:

```
POST /api/v1/admin/tracks/{id}/audio/upload-init
     → {"upload_url": "<presigned PUT в бакет masters, TTL 1 ч, лимит 200 МБ>",
        "key": "{audio_uuid}/original.flac"}
     (клиент делает PUT файла напрямую в S3-эндпоинт R2)

POST /api/v1/admin/tracks/{id}/audio/upload-complete
     → Django делает HEAD на объект (существует, размер сходится),
       ставит audio_status='processing' и Celery-цепочку в очередь media

GET  /api/v1/admin/tracks/{id}/audio/status
     → uploaded | processing | ready | failed
```

Статусы совпадают с `CHECK`-констрейнтом `tracks.audio_status` (см. «Модель данных») и используются в этом виде везде: в API, в админке, в мониторинге.

Celery-цепочка (очередь `media` на отдельной ноде — см. «Инфраструктура»):

1. **Валидация** — `ffprobe -v error -print_format json -show_format -show_streams`:
   - контейнер/кодек в allowlist: FLAC, WAV, ALAC, MP3 ≥320 CBR (лучше требовать lossless-мастер);
   - sample rate ≥ 44100 Гц, каналов ≤ 2, ровно один аудиопоток, нет видеопотоков;
   - длительность 30 сек – 15 мин; файл дочитывается до конца (защита от битых загрузок).
   Провал → статус `failed` с machine-readable причиной (видна в админке).
2. **Извлечение метаданных** — `mutagen`: теги (title/artist/album/tracknumber), точная `duration_ms`, встроенная обложка (извлекаем как кандидата). Теги только **предзаполняют** форму каталога — источник истины всегда PostgreSQL, не ID3.
3. **Анализ громкости** — `ffmpeg -i in.flac -af ebur128 -f null -`, парсим integrated loudness → пишем `tracks.loudness_lufs NUMERIC(5,2)`. Клиенты нормализуют громкость к −14 LUFS (как Spotify), применяя gain на своей стороне — иначе плейлисты «скачут» по громкости.
4. **Транскодирование** — три параллельные Celery-задачи (chord), по одной на битрейт 96/160/320:
   ```bash
   ffmpeg -i original.flac -map 0:a:0 -vn \
     -c:a aac -b:a 160k -ar 44100 -ac 2 \
     -movflags +faststart -f mp4 aac_160.m4a
   ```
   **Gapless:** AAC-энкодер добавляет encoder delay (~2048 сэмплов) в начало и padding в конец потока — если плеер о них не знает, между треками концертного альбома или микса слышны щелчки и микропаузы. ffmpeg записывает gapless-метаданные в MP4 (тег `iTunSMPB` в `udta` и `edts/elst`); пайплайн проверяет их наличие через ffprobe, а в CI держим регрессионный тест на паре «встык склеенных» эталонных треков. ExoPlayer, AVPlayer и современные браузеры эти метаданные учитывают.
5. **Публикация** — заливка трёх файлов в бакет `audio` с заголовками `Content-Type: audio/mp4` и `Cache-Control: public, max-age=31536000, immutable`, вставка строк в `track_media`, затем `audio_status = 'ready'`.

Производительность: AAC-энкод идёт ~50–80× реального времени на ядро → трек 3,5 мин во все три битрейта ≈ 10–15 сек CPU. Первичный импорт каталога в 100 тыс. треков ≈ 300–400 ядро-часов — **временная 8-ядерная машина** под очередь `media` справится за ~2 суток и затем удаляется; постоянной мощности под это держать не нужно.

### 4.5. Объектное хранилище: сравнение и выбор

Все четыре варианта S3-совместимы (boto3 работает со всеми), различие — в цене egress-трафика, и именно она убивает бюджет стриминга. Считаем от канона «Допущений»: **32 ТБ исходящего трафика в месяц, хранение ~4,6 ТБ**.

| Хранилище | Хранение | Egress | Итог/мес на нашей нагрузке | Комментарий |
|---|---|---|---|---|
| **Cloudflare R2** | $0,015/ГБ | **$0** | **~$74** ($69 storage + $5 Workers) | нулевой egress, нативная связка с Cloudflare CDN |
| Backblaze B2 | $6/ТБ | $0,01/ГБ, бесплатно через Cloudflare (Bandwidth Alliance) | ~$30 | дешевле всех по хранению, но связка из двух вендоров |
| AWS S3 + CloudFront | $0,023/ГБ | ~$0,085/ГБ | **~$2 800** (из них ~$2 700 — egress 32 ТБ) | egress на порядки дороже всего остального счёта |
| MinIO (self-hosted) | цена сервера | цена канала | ~€80 (Hetzner с 2×8 ТБ) + ваше время | админить, бэкапить, мониторить самим |

**Решение: Cloudflare R2.** Нулевой egress означает, что рост прослушиваний не растит счёт вообще; CDN и токен-валидация живут у того же вендора; операции копеечные (Class A $4,50/млн — импорт 100 тыс. треков это разовые ~$5; Class B $0,36/млн — при CDN-кэше почти не тратятся). B2 — запасной вариант, если хранение вырастет до десятков ТБ (миграция — один прогон rclone); он же используется как второе хранилище для бэкапа мастеров (см. «Инфраструктура», бэкапы). MinIO — только при жёстком требовании держать данные на своём железе; на старте это лишняя операционка. AWS S3 для стриминга не рекомендуется в принципе — egress-модель против нас.

### 4.6. CDN

Cloudflare CDN перед R2 (кастомный домен `media.example.com`). Зачем при «всего» тысячах пользователей:

- **Range из кэша**: Cloudflare кэширует объект целиком и раздаёт произвольные Range-куски из кэша — перемотки не бьют в origin;
- **TLS-терминация и HTTP/2/3 близко к слушателю**: время до первого байта — десятки мс вместо сотен, старт трека мгновенный;
- **Кэш горячего контента**: прослушивания подчиняются Ципфу — верхние ~10% треков дают ~80% трафика, реалистичный hit ratio **70–85%** (эта же цифра — база для алерта мониторинга, см. «Инфраструктура»);
- **Origin разгружен**: R2 видит только промахи кэша.

Файлы иммутабельны (версионирование ключей — ниже), поэтому `Cache-Control: public, max-age=31536000, immutable`, инвалидация не нужна никогда.

### 4.7. Защита контента: подписанные URL с TTL

**Presigned-URL от S3 напрямую здесь не годятся**: у каждого пользователя уникальная query-строка, поэтому CDN промахивается мимо кэша, а если настроить кэш «игнорировать query string», кэш начнёт отдавать контент вообще без проверки подписи. Правильная схема: **своя HMAC-подпись, которую валидирует edge (Cloudflare Worker), а ключом кэша остаётся чистый путь**.

Django генерирует URL (~50 строк кода, ноль зависимостей):

```python
import hashlib, hmac, time
from django.conf import settings

def signed_stream_url(audio_uuid: str, rev: int, quality: str, ttl: int = 6 * 3600) -> str:
    expires = int(time.time()) + ttl
    path = f"/{audio_uuid}/v{rev}/aac_{BITRATES[quality]}.m4a"   # /…/aac_160.m4a
    token = hmac.new(settings.MEDIA_SIGNING_KEY.encode(),
                     f"{path}:{expires}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"https://media.example.com{path}?e={expires}&t={token}"
```

Worker на edge (~30 строк): пересчитывает HMAC от `path:e`, сверяет с `t`, проверяет `e > now`; при успехе отдаёт объект из R2-биндинга, кэшируя по пути без query. Провал — 403. При self-hosted MinIO та же схема реализуется штатным nginx `secure_link_md5` перед MinIO — Django-код не меняется.

Параметры: **TTL подписи 6 часов** — перекрывает любую сессию прослушивания с паузами и перемотками, но украденная ссылка умирает в тот же день. Ключ подписи (`MEDIA_SIGNING_KEY`) — в секретах с возможностью ротации: Worker принимает два ключа (старый и новый) на период ротации.

**Почему не DRM на старте.** Widevine (Android/веб) + FairPlay (Apple, требует HLS) — это три платформенные интеграции, лицензионный сервер (или SaaS вроде EZDRM/PallyCon с оплатой за лицензию), EME-код в веб-плеере и месяцы работы. При этом DRM у Spotify и прочих всё равно обходится, т.е. защищает лишь от казуального копирования — ровно как и подписанные URL (файл нельзя скачать без валидного токена, токены не гуглятся, TTL ограничен). Настоящий триггер для DRM — не техника, а **контракты с мейджор-лейблами**, которые его требуют. Пока таких контрактов нет — не внедряем; когда появятся — переходим на HLS + Multi-DRM SaaS (см. «Путь масштабирования»).

### 4.8. Обложки и изображения

Отдельный публичный бакет `images` за тем же CDN (`img.example.com`), **без подписей** — обложки не являются защищаемым контентом, а подписи убили бы кэшируемость `<img>`.

- Приём: JPEG/PNG, минимум 640×640, максимум 4000×4000 и 10 МБ; квадрат (принудительный центр-кроп). Загрузка — тем же механизмом presigned PUT, что и аудио.
- Ресайзы в Celery через Pillow (при желании pyvips — быстрее, но на этом масштабе не обязателен): **64** (списки), **300** (карточки/сетки), **640** (экран плеера). Формат **WebP q80** — в 2026 поддержан всеми целевыми платформами, минус 30% к весу против JPEG; оригинал сохраняем как есть для будущих перересайзов.
- Вес: 640.webp ≈ 50–80 КБ, 300.webp ≈ 20–30 КБ, 64.webp ≈ 3–5 КБ.
- Кэш-бастинг — контент-хэш в ключе (`{sha8}` — первые 8 hex-символов SHA-256 содержимого оригинала), заголовок `immutable`. Текущий ключ хранится в PostgreSQL рядом с сущностью (`albums.cover_key`, `artists.image_key`, `playlists.cover_key`, `user_profiles.avatar_key`); API отдаёт клиентам уже готовые абсолютные URL всех размеров.

### 4.9. Структура бакетов и ключей

Три бакета с разными политиками доступа. В ключах **аудио** — UUID (`tracks.audio_uuid`), а не последовательные id: исключаем перебор ключей защищаемого контента. В ключах **изображений** — обычные BIGINT-id сущностей: контент публичный, перебор не угроза, а иммутабельность обеспечивает `{sha8}`; исключение — аватары, где используется `users.public_id`, чтобы не светить порядковый номер пользователя.

```
Бакет masters — приватный, доступ только у пайплайна (никогда не отдаётся наружу)
  {audio_uuid}/original.flac

Бакет audio — приватный, наружу только через CDN + Worker (HMAC)
  {audio_uuid}/v{rev}/aac_96.m4a
  {audio_uuid}/v{rev}/aac_160.m4a
  {audio_uuid}/v{rev}/aac_320.m4a

Бакет images — публичное чтение через CDN
  albums/{album_id}/{sha8}/original.jpg
  albums/{album_id}/{sha8}/640.webp
  albums/{album_id}/{sha8}/300.webp
  albums/{album_id}/{sha8}/64.webp
  artists/{artist_id}/{sha8}/…            (тот же набор)
  playlists/{playlist_id}/{sha8}/…        (обложки пользовательских плейлистов)
  avatars/{user_public_id}/{sha8}/…
```

`v{rev}` — целочисленная ревизия транскода (`tracks.audio_rev` в PostgreSQL): при перетранскодировании (замена мастера, новый пресет) пишем `v2` и переключаем поле — старые CDN-кэши инвалидировать не нужно, старые файлы чистим фоновой задачей. На бакете `masters` включено версионирование объектов, и он реплицируется во второе хранилище — FLAC-мастера невосстановимы (детали — «Инфраструктура», бэкапы).

### 4.10. Оценка объёмов

Средний трек 3,5 мин:

| Артефакт | Размер |
|---|---|
| aac_96 + aac_160 + aac_320 | 2,5 + 4,2 + 8,4 = **15,1 МБ** |
| Мастер FLAC | ~30 МБ |
| Итого на трек | **~45 МБ** |

Каталог 100 тыс. треков: транскоды **~1,5 ТБ**, мастера **~3 ТБ** (хранить обязательно — источник всех будущих перетранскодов в Opus/HLS/lossless-тир), изображения ~30–50 тыс. обложек × ~1,2 МБ ≈ **~50 ГБ**. Всего **~4,6 ТБ ≈ $69/мес** в R2. Трафик ~32 ТБ/мес — в R2 бесплатен.

### 4.11. Офлайн-прослушивание на мобильных

Отдельного «формата для скачивания» не нужно — клиент скачивает **те же `.m4a` по тому же подписанному URL** (обычно `high` на Wi-Fi). От бекенда требуется:

1. **Учёт скачиваний** — таблица `offline_downloads` (DDL — в «Модели данных»; `device_id BIGINT` — FK на `user_devices.id`). Лимиты в бизнес-логике из тарифа: ≤ `plans.max_offline_devices` устройств с офлайн-загрузками на аккаунт (дефолт 5), ≤10 000 треков на устройство (цифры Spotify — разумный дефолт). Лимит одновременных стримов — отдельное поле `plans.max_concurrent_streams`, к офлайну отношения не имеет.
2. **Периодическая ревалидация** — `POST /api/v1/offline/sync` c `device_id` и списком track_id (или его хэшем): сервер подтверждает право (подписка активна, треки не изъяты из каталога) и возвращает список отозванных. Клиент обязан успешно синхронизироваться **раз в 30 дней**, иначе локально помечает загрузки недоступными — это и есть вся «офлайн-лицензия» без DRM.
3. **Шифрование файлов — на клиенте**: AES-ключ в Android Keystore / iOS Keychain, файлы не лежат в открытом виде в файловой системе. Бекенд ключами не управляет; честно понимаем, что это защита от казуального доступа, не DRM — на старте этого достаточно.
4. Скачивание помечается `"intent": "download"` в теле запроса `POST /stream` — для аналитики и отдельного троттлинг-порога (скачивание альбома — легитимный всплеск из десятков запросов, в отличие от такого же всплеска стримов).

Эволюция доставки — Opus-транскоды, HLS + DRM, lossless-тир, гео-распределение — собрана в «Пути масштабирования».

---

## 5. Модель данных: что и где хранится

### 5.1. Общие принципы

- **Единственный источник истины — PostgreSQL 16.** Всё остальное (Redis, поисковый индекс, CDN-кэш) — производные или временные данные, которые можно потерять и восстановить из PG.
- **Первичные ключи — `BIGINT GENERATED ALWAYS AS IDENTITY`.** Компактнее и быстрее в join'ах, чем UUID; для каталога (артисты/альбомы/треки) последовательные id не являются секретом, и **наружу в API и в поисковые документы отдаются именно целые id**. UUID существуют ровно в двух местах: `users.public_id` (наружу не отдаём порядковый номер пользователя) и `tracks.audio_uuid` (ключи аудиофайлов в бакетах — защита от перебора, см. «Хранение и доставка аудио»). Переходить на UUIDv7 повсеместно стоит только при мультирегиональной записи или merge баз — на этом масштабе не нужно.
- **Все временные поля — `TIMESTAMPTZ`, хранение в UTC.** Часовой пояс — забота клиента.
- **Enum-поля — `VARCHAR` + `CHECK`-constraint** (Django `TextChoices`), а не нативные PG enum: добавление значения — обычная миграция без `ALTER TYPE` и блокировок.
- **Деньги — целые в минимальных единицах** (`price_cents INT`), никаких float.
- **Файлы (аудио, обложки) в БД не лежат.** В таблицах хранятся только ключи объектного хранилища (`TEXT`), полные URL собираются на лету; смена CDN-домена или бакета не требует миграции данных.
- Django автоматически создаёт индекс на каждый `ForeignKey` — ниже перечислены только дополнительные индексы сверх этого.

Размеры считаем от «Допущений»: ~100 тыс. регистраций, 10 тыс. DAU, 2–3 тыс. одновременных, каталог 100 тыс. треков.

### 5.2. PostgreSQL: пользователи, профили, тарифы, подписки

```sql
-- Кастомная модель Django-пользователя (AUTH_USER_MODEL), только аутентификация
CREATE TABLE users (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id         UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE, -- наружу в API
    email             CITEXT NOT NULL UNIQUE,       -- citext: регистронезависимая уникальность
    password          VARCHAR(128) NOT NULL,        -- хэш Django (argon2)
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    is_staff          BOOLEAN NOT NULL DEFAULT FALSE,
    email_verified_at TIMESTAMPTZ,
    date_joined       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login        TIMESTAMPTZ
);

-- Профиль 1:1 — всё, что не касается аутентификации
CREATE TABLE user_profiles (
    user_id           BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    display_name      VARCHAR(120) NOT NULL,
    avatar_key        TEXT,                          -- ключ в бакете images
    country           CHAR(2),                       -- ISO 3166-1
    birth_date        DATE,
    language          VARCHAR(8) NOT NULL DEFAULT 'ru',
    preferred_quality VARCHAR(8) NOT NULL DEFAULT 'normal'
                      CHECK (preferred_quality IN ('low','normal','high')),
    settings          JSONB NOT NULL DEFAULT '{}',   -- редкие флаги UI; НЕ свалка бизнес-полей
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Справочник тарифов (строк — единицы, правится руками/админкой)
CREATE TABLE plans (
    id                     SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code                   VARCHAR(32) NOT NULL UNIQUE,   -- 'free' | 'premium' | 'family'
    name                   VARCHAR(120) NOT NULL,
    price_cents            INT NOT NULL DEFAULT 0,
    currency               CHAR(3) NOT NULL DEFAULT 'RUB',
    max_concurrent_streams SMALLINT NOT NULL DEFAULT 1,   -- одновременные стримы
    max_offline_devices    SMALLINT NOT NULL DEFAULT 5,   -- устройства с офлайн-загрузками
    max_quality            VARCHAR(8) NOT NULL DEFAULT 'normal', -- потолок битрейта тарифа
    trial_days             SMALLINT NOT NULL DEFAULT 0,
    is_active              BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE subscriptions (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id               BIGINT NOT NULL REFERENCES users(id),
    plan_id               SMALLINT NOT NULL REFERENCES plans(id),
    status                VARCHAR(16) NOT NULL
                          CHECK (status IN ('trialing','active','past_due','canceled','expired')),
    started_at            TIMESTAMPTZ NOT NULL,
    current_period_end    TIMESTAMPTZ NOT NULL,      -- по нему решаем «премиум ли ещё»
    canceled_at           TIMESTAMPTZ,
    external_customer_id  VARCHAR(255),              -- id в платёжном провайдере
    external_sub_id       VARCHAR(255),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Не более одной «живой» подписки на пользователя:
CREATE UNIQUE INDEX uq_subscriptions_one_live ON subscriptions (user_id)
    WHERE status IN ('trialing','active','past_due');
```

`max_concurrent_streams` и `max_offline_devices` — два разных лимита: первый ограничивает параллельное воспроизведение (механизм enforcement — за рамками MVP, см. последний раздел), второй — число устройств с офлайн-библиотекой. История подписок сохраняется как строки со статусом `expired`/`canceled` — отдельная audit-таблица не нужна.

### 5.3. PostgreSQL: каталог

```sql
CREATE TABLE artists (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         VARCHAR(255) NOT NULL,
    slug         VARCHAR(255) NOT NULL UNIQUE,       -- для человекочитаемых URL веба
    aliases      TEXT[] NOT NULL DEFAULT '{}',       -- редакторские альтернативные написания
    bio          TEXT,
    image_key    TEXT,                                -- ключ фото в бакете images
    is_verified  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Fallback-поиск по ILIKE '%...%', пока/если поисковый движок недоступен:
CREATE INDEX ix_artists_name_trgm ON artists USING gin (name gin_trgm_ops);

CREATE TABLE albums (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title             VARCHAR(500) NOT NULL,
    primary_artist_id BIGINT NOT NULL REFERENCES artists(id),
    album_type        VARCHAR(16) NOT NULL DEFAULT 'album'
                      CHECK (album_type IN ('album','single','ep','compilation')),
    release_date      DATE,
    cover_key         TEXT,                           -- ключ оригинала обложки в бакете images
    upc               VARCHAR(20),                    -- штрихкод релиза, для дедупликации при инжесте
    label             VARCHAR(255),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_albums_artist_release ON albums (primary_artist_id, release_date DESC);
CREATE INDEX ix_albums_release ON albums (release_date DESC);  -- «новинки» в discovery

CREATE TABLE tracks (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    album_id       BIGINT NOT NULL REFERENCES albums(id),
    title          VARCHAR(500) NOT NULL,
    duration_ms    INT NOT NULL CHECK (duration_ms > 0),
    disc_number    SMALLINT NOT NULL DEFAULT 1,
    track_number   SMALLINT NOT NULL,
    is_explicit    BOOLEAN NOT NULL DEFAULT FALSE,
    isrc           CHAR(12) UNIQUE,                   -- международный код записи, nullable
    audio_uuid     UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE, -- ключи в бакетах masters/audio
    audio_rev      SMALLINT NOT NULL DEFAULT 1,       -- ревизия транскода: v{rev} в ключах
    audio_status   VARCHAR(12) NOT NULL DEFAULT 'uploaded'
                   CHECK (audio_status IN ('uploaded','processing','ready','failed')),
    loudness_lufs  NUMERIC(5,2),                      -- integrated loudness, пишет пайплайн
    popularity     SMALLINT NOT NULL DEFAULT 0
                   CHECK (popularity BETWEEN 0 AND 100), -- пересчёт ночью, см. «Поиск»
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_tracks_album_pos ON tracks (album_id, disc_number, track_number);
CREATE INDEX ix_tracks_title_trgm ON tracks USING gin (title gin_trgm_ops);
-- В выдачу API попадают только треки audio_status = 'ready'

-- Несколько исполнителей на трек (фиты)
CREATE TABLE track_artists (
    track_id   BIGINT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    artist_id  BIGINT NOT NULL REFERENCES artists(id),
    role       VARCHAR(16) NOT NULL DEFAULT 'primary' CHECK (role IN ('primary','featured')),
    position   SMALLINT NOT NULL DEFAULT 0,           -- порядок отображения имён
    PRIMARY KEY (track_id, artist_id)
);
CREATE INDEX ix_track_artists_artist ON track_artists (artist_id); -- «все треки артиста»

-- Аудиофайлы трека: одна строка = один файл в объектном хранилище
CREATE TABLE track_media (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    track_id    BIGINT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    quality     VARCHAR(16) NOT NULL
                CHECK (quality IN ('flac_source','aac_96','aac_160','aac_320')),
    s3_key      TEXT NOT NULL,                        -- ключ в бакете masters либо audio
    size_bytes  BIGINT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (track_id, quality)
);

CREATE TABLE genres (
    id         SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       VARCHAR(120) NOT NULL,
    slug       VARCHAR(120) NOT NULL UNIQUE,
    parent_id  SMALLINT REFERENCES genres(id)         -- иерархия: rock -> indie rock
);

CREATE TABLE track_genres (
    track_id  BIGINT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    genre_id  SMALLINT NOT NULL REFERENCES genres(id),
    PRIMARY KEY (track_id, genre_id)
);
CREATE INDEX ix_track_genres_genre ON track_genres (genre_id);
```

`artists.aliases` — редакторские альтернативные написания имени («Кино» → `{"kino"}`, «The Beatles» → `{"битлз", "beatles"}`) — основной канал кросс-алфавитного поиска, попадает в поисковые документы (см. «Поиск и обнаружение контента»). Жанры вешаем на треки; «жанры артиста» — производная величина (топ жанров его треков), пересчитывается ночным Celery-таском в денормализованное поле при необходимости.

### 5.4. PostgreSQL: плейлисты и порядок треков

```sql
CREATE TABLE playlists (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id         BIGINT NOT NULL REFERENCES users(id),
    title            VARCHAR(255) NOT NULL,
    description      TEXT,
    is_public        BOOLEAN NOT NULL DEFAULT FALSE,
    is_collaborative BOOLEAN NOT NULL DEFAULT FALSE,
    cover_key        TEXT,                            -- NULL => клиент рисует коллаж 2x2 из обложек
    tracks_count     INT NOT NULL DEFAULT 0,          -- денормализация для списков
    total_ms         BIGINT NOT NULL DEFAULT 0,       -- обновляются в одной транзакции с составом
    followers_count  INT NOT NULL DEFAULT 0,          -- пересчёт ночным Celery-таском
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_playlists_owner ON playlists (owner_id, updated_at DESC);

CREATE TABLE playlist_tracks (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    playlist_id  BIGINT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id     BIGINT NOT NULL REFERENCES tracks(id),
    added_by_id  BIGINT NOT NULL REFERENCES users(id), -- важно для коллаборативных
    position     INT NOT NULL,
    added_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (playlist_id, position) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ix_playlist_tracks_track ON playlist_tracks (track_id); -- «в каких плейлистах трек»
```

`followers_count` — денормализованный счётчик строк `playlist_followers`; нужен ранжированию публичных плейлистов в поиске (см. «Поиск и обнаружение контента») и карточкам плейлистов. Точность «до суток» достаточна, поэтому пересчёт — ночной Celery-таск, а не триггер.

**Порядок треков: разреженный `INT position`, а не fractional indexing.**

- Вставка в конец: `position = max(position) + 1024`. Вставка между A и B: `position = (A+B)/2`.
- Если между соседями зазора нет ((B−A) = 1) — перенумеровываем весь плейлист с шагом 1024 одним `UPDATE` в той же транзакции. Для плейлиста даже в 10 тыс. треков это миллисекунды, а случается редко. Constraint `DEFERRABLE` — чтобы перенумерация не спотыкалась об уникальность в середине транзакции.
- **Дубликаты трека в плейлисте разрешены** (как в Spotify), поэтому уникальности по `(playlist_id, track_id)` нет — только суррогатный `id`. Именно он — стабильная ссылка на «строку плейлиста» в API: `DELETE/PATCH /playlists/{id}/tracks/{entry_id}` (см. «Django-бекенд и API»).
- **Fractional indexing (строковые ключи типа LexoRank)** даёт O(1)-вставку без перенумераций и нужен, когда один плейлист параллельно редактируют многие пользователи в реальном времени. Для нашего масштаба это переусложнение: конфликт двух одновременных вставок в одну позицию решается ретраем транзакции.

### 5.5. PostgreSQL: лайки, избранное, подписки на артистов

Все таблицы — «связка + время», композитный PK экономит индекс и запрещает дубли:

```sql
CREATE TABLE liked_tracks (
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    track_id   BIGINT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, track_id)
);
-- Экран «Любимые треки» = выборка по user_id, сортировка по дате лайка:
CREATE INDEX ix_liked_tracks_user_time ON liked_tracks (user_id, created_at DESC);
CREATE INDEX ix_liked_tracks_track ON liked_tracks (track_id); -- счётчик лайков трека

CREATE TABLE liked_albums (
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    album_id   BIGINT NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, album_id)
);

CREATE TABLE artist_follows (
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    artist_id  BIGINT NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, artist_id)
);

CREATE TABLE playlist_followers (            -- добавление чужого публичного плейлиста в библиотеку
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    playlist_id BIGINT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, playlist_id)
);
```

Лайк = вставка/удаление строки, идемпотентно через `INSERT ... ON CONFLICT DO NOTHING`. Никаких счётчиков лайков в `tracks` в реальном времени — агрегаты считаются ночью (в `popularity` и `playlists.followers_count`).

### 5.6. PostgreSQL: история прослушиваний — самая большая таблица

**Что считается «прослушиванием».** Единственный канал учёта — батчевые клиентские события `POST /api/v1/me/playback-events` (контракт — в «Django-бекенд и API»; выдача stream-URL в историю **ничего не пишет**). Сервер помечает событие `is_counted = true`, если `ms_played >= 30000` **или** `ms_played >= duration_ms / 2` (правило «30 секунд или 50% трека» — короткие треки засчитываются с половины). В `play_history` пишутся **все** события (нужны для «недавно прослушанного» и рекомендаций), но в счётчики популярности и чарты идут только `is_counted`. Дедупликация накруток — Redis-ключом `play:dedup` (см. §5.8) плюс серверная проверка: не более одного зачтённого прослушивания трека пользователем в 30 секунд.

```sql
CREATE TABLE play_history (
    id         BIGINT GENERATED ALWAYS AS IDENTITY,
    user_id    BIGINT NOT NULL REFERENCES users(id),
    track_id   BIGINT NOT NULL REFERENCES tracks(id),
    device_id  BIGINT REFERENCES user_devices(id),
    played_at  TIMESTAMPTZ NOT NULL,            -- момент старта воспроизведения
    ms_played  INT NOT NULL,
    is_counted BOOLEAN NOT NULL,
    source     VARCHAR(16)                      -- откуда запущен трек
               CHECK (source IN ('playlist','album','liked','search','artist')),
    source_id  BIGINT,                          -- id плейлиста/альбома/артиста-источника
    PRIMARY KEY (played_at, id)                 -- ключ партиционирования обязан войти в PK
) PARTITION BY RANGE (played_at);

-- Партиции по месяцам, создаются Celery-таском (или pg_partman) на 2 месяца вперёд:
CREATE TABLE play_history_2026_08 PARTITION OF play_history
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

CREATE INDEX ix_play_history_user ON play_history (user_id, played_at DESC); -- «недавно прослушанное»
CREATE INDEX ix_play_history_track ON play_history (track_id, played_at);    -- статистика трека
```

**Django и партиции:** `PARTITION BY RANGE` не выражается штатными Django-миграциями. Варианты: `django-postgres-extra` (модель `PostgresPartitionedModel` + management-команда для партиций) либо raw SQL (`migrations.RunSQL`) в обычной миграции + Celery-таск создания партиций. Оба рабочие; главное — партиционирование заводится первой же миграцией, конвертировать заполненную таблицу потом дорого.

**Оценка роста** (из «Допущений»): ~400 тыс. строк/день ≈ **12 млн строк/месяц ≈ 1,5–2 ГБ/месяц с индексами**. PostgreSQL спокойно живёт с этим годами.

**Политика удержания: 12 «горячих» месячных партиций.** Партиции старше года просто удаляются (`DROP TABLE` партиции — мгновенная операция без vacuum-долга). Никаких выгрузок в parquet и промежуточных архивов: при 1,5–2 ГБ/мес это лишняя движущаяся часть, к тому же осложняющая удаление данных пользователя. Пользовательский экран истории глубже года не листает — ограничение никого не задевает. Ретенция в 90 дней действует только для `search_history` (§5.7), не для прослушиваний.

**Когда пора в ClickHouse:** (а) объём превысил ~500 млн строк / сотни ГБ, (б) появились продуктовые аналитические запросы (когорты, фичи для рекомендаций, дашборды по всему массиву), которые кладут реплику PG. Тогда события начинают дублироваться в ClickHouse, а в PG остаются последние 90 дней для пользовательского UI (см. «Путь масштабирования»). До этого порога ClickHouse — лишняя движущаяся часть.

**Агрегаты** (чтобы не считать `COUNT(*)` по сырым событиям на каждый чих):

```sql
CREATE TABLE track_play_counts (        -- накопительный счётчик, из Redis-буфера раз в 60 с
    track_id    BIGINT PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    total_plays BIGINT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE track_stats_daily (        -- для чартов и пересчёта popularity, пишется ночным таском
    track_id  BIGINT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    day       DATE NOT NULL,
    plays     INT NOT NULL DEFAULT 0,
    listeners INT NOT NULL DEFAULT 0,   -- уникальные пользователи
    PRIMARY KEY (track_id, day)
);
```

Поток данных один: клиентские события → `play_history` (все) + `HINCRBY plays:buf` (только зачтённые) → сброс буфера в `track_play_counts` раз в 60 секунд → ночная агрегация `play_history` в `track_stats_daily` → ночной пересчёт `popularity` и чарты. Других каналов учёта прослушиваний нет.

### 5.7. PostgreSQL: поисковая история, устройства, офлайн-загрузки

Единственное определение `search_history` (используется поиском — см. «Поиск и обнаружение контента», §6.8):

```sql
CREATE TABLE search_history (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    query        VARCHAR(255) NOT NULL,
    result_count INT NOT NULL DEFAULT 0,
    clicked_type VARCHAR(16),          -- track|album|artist|playlist, NULL = клика не было
    clicked_id   BIGINT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_search_history_user ON search_history (user_id, created_at DESC);
-- Отчёт по нулевым запросам (источник синонимов и алиасов):
CREATE INDEX ix_search_history_zero ON search_history (created_at) WHERE result_count = 0;
-- Ретенция 90 дней: ночной Celery-таск удаляет старые строки батчами (объём мизерный,
-- партиционирование не нужно)

CREATE TABLE user_devices (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    fingerprint  UUID NOT NULL,          -- генерирует клиент при установке/первом входе
    kind         VARCHAR(8) NOT NULL CHECK (kind IN ('web','android','ios')),
    name         VARCHAR(120),           -- 'Pixel 9', 'Chrome on Windows'
    app_version  VARCHAR(32),
    last_ip      INET,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at   TIMESTAMPTZ,            -- «выйти на этом устройстве»
    UNIQUE (user_id, fingerprint)
);

CREATE TABLE offline_downloads (
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id  BIGINT NOT NULL REFERENCES user_devices(id) ON DELETE CASCADE,
    track_id   BIGINT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    quality    VARCHAR(8) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    PRIMARY KEY (user_id, device_id, track_id)
);
```

`user_devices` стыкуется с аутентификацией (refresh-токен привязан к устройству, отзыв устройства инвалидирует токен) и с офлайн-режимом (лимит `plans.max_offline_devices`).

### 5.8. Redis: два инстанса и точный перечень содержимого

`maxmemory-policy` в Redis задаётся **на инстанс, а не на логическую БД** — поэтому «кэш с lru и очередь с noeviction в одном Redis на разных `/0` `/1`» технически невозможны. Разворачиваем **два инстанса** Redis 7:

| Инстанс | Размер | Политика | Что живёт |
|---|---|---|---|
| **redis-cache** | 2 ГБ | `allkeys-lru` | кэши API, лайк-сеты, чарты, rate limiting — всё, что можно молча потерять |
| **redis-queue** | 1 ГБ | `noeviction` | брокер и результаты Celery, буфер счётчиков, дедупликация событий — то, что вытеснять нельзя |

Перечень ключей:

| Назначение | Пример ключа | Инстанс | Тип | TTL |
|---|---|---|---|---|
| Кэш горячих ответов API (альбом с треками, страница артиста) | `cache:v1:album:8412` | cache | string (JSON) | 15 мин + явная инвалидация из `services.py` при записи |
| Кэш главной/чартов | `cache:v1:home:ru` | cache | string (JSON) | 60 с |
| Готовые чарты (списки id) | `chart:global:week` | cache | ZSET по plays | 7 ч, перезапись beat-таском каждые 6 ч |
| «Id лайкнутых треков» для сердечек в выдаче | `likes:u:123` | cache | set of track_id | 30 мин, sync при лайке |
| Rate limiting (DRF-троттлинг по scope: search, stream, auth…) | `rl:u:123:search:202608031205` | cache | INCR + EXPIRE | окно 60 с |
| Буфер счётчиков прослушиваний | `plays:buf` | queue | hash {track_id: n}, HINCRBY | без TTL; beat раз в 60 с: `HGETALL`+`DEL` → батч-UPSERT в `track_play_counts` |
| Дедупликация play-событий (даблклики/накрутка) | `play:dedup:123:8412` (user:track) | queue | string, SET NX | 30 с |
| Очередь Celery (брокер + результаты) | `celery` (list) | queue | — | — |

Принцип: **в Redis нет ничего, что нельзя потерять.** Падение redis-cache = холодный кэш. Падение redis-queue = потеря максимум минуты счётчиков (сырые события лежат в `play_history` — счётчики восстановимы пересчётом) и невыполненных задач очереди (все задачи идемпотентны). Отозванные refresh-токены здесь **не** хранятся — blacklist живёт в PostgreSQL (см. «Django-бекенд и API», аутентификация): отзыв — редкое событие, надёжность важнее микросекунд.

### 5.9. Объектное хранилище

Три бакета Cloudflare R2 — `masters` (приватный), `audio` (приватный, наружу через CDN + Worker), `images` (публичный за CDN). Полная раскладка ключей, версионирование `v{rev}` и `{sha8}` — в «Хранении и доставке аудио», §4.9. В PostgreSQL хранятся только ключи: `track_media.s3_key`, `albums.cover_key`, `artists.image_key`, `playlists.cover_key`, `user_profiles.avatar_key`.

### 5.10. Поисковый индекс — производная копия

Meilisearch (выбор и настройка — в «Поиске и обнаружении контента»). С точки зрения модели данных важно одно: **индекс — не источник истины, а материализованное представление PostgreSQL**.

- Индексируются денормализованные документы: трек = `{id, title, artist_names[], album_title, genre, popularity, …}`; аналогично `artists`, `albums`, `playlists` (только публичные). `id` документа = BIGINT PK из PG — тот же id, что отдаёт REST API, клиент склеивает поиск с каталогом без преобразований.
- Синхронизация: явный вызов из `services.py` каталога → `transaction.on_commit()` → Celery-таск upsert/delete (код — в «Поиске», §6.4). Плюс ночная сверка консистентности с автоматическим запуском переиндексации при расхождении (см. «Поиск», §6.4) — самовосстановление после любых рассинхронов.
- Индекс можно в любой момент удалить и перестроить из PG: 100 тыс. треков — это маленький индекс (~50–100 МБ), полная переиндексация — 1–2 минуты.

### 5.11. Сводная таблица: тип данных → хранилище → почему

| Данные | Хранилище | Почему именно там |
|---|---|---|
| Пользователи, подписки, каталог, плейлисты, лайки | PostgreSQL | Источник истины: транзакции, FK-целостность, реляционные данные по природе |
| FLAC-мастера | R2, бакет `masters` (+версионирование, +реплика в B2) | Невосстановимы; источник всех будущих транскодов; наружу не отдаются |
| AAC-транскоды | R2, бакет `audio` + CDN (HMAC-Worker) | Дёшево, масштабируется отдельно от БД; Django раздачей не занимается |
| Обложки, аватары | R2, бакет `images` + CDN | Публичная статика; иммутабельность через контент-хэш в ключе |
| События прослушиваний | PostgreSQL, месячные партиции (12 горячих) | 12 млн строк/мес — комфортно для PG; DROP партиции = дешёвое удаление старого |
| Агрегаты прослушиваний | PostgreSQL (`track_play_counts`, `track_stats_daily`) | Чтобы не сканировать сырые события; наполняются из Redis-буфера и ночных тасков |
| Кэш ответов API, лайк-сеты, чарты, rate limiting | redis-cache (`allkeys-lru`), TTL 1–15 мин | Снимает повторяющиеся чтения каталога с PG; потеря безболезненна |
| Буфер счётчиков, дедупликация событий | redis-queue (`noeviction`) → сброс в PG раз в 60 с | HINCRBY вместо UPDATE горячей строки PG на каждое воспроизведение; нужны атомарные INCR/SETNX + TTL |
| Очередь фоновых задач | redis-queue (Celery broker) | На этом масштабе выделенный RabbitMQ/Kafka не окупается |
| Отозванные refresh-токены | PostgreSQL (`token_blacklist` из simplejwt) | Отзыв редкий; надёжность и простота важнее скорости |
| Поисковый индекс | Meilisearch | Производная копия PG; typo-tolerance и релевантность, которых нет в LIKE/FTS; перестраивается за минуты |

---

## 6. Поиск и обнаружение контента

### 6.1. Выбор поискового движка

**Нагрузка** (из «Допущений»): каталог 100 тыс. треков, ~20–30 тыс. альбомов, ~10–15 тыс. артистов, тысячи публичных плейлистов. Документ трека ~0,5 КБ → суммарный индекс **~50–100 МБ**. Search-as-you-type с debounce 250–300 мс даёт пик **~30–80 RPS** на поисковый эндпоинт — это очень мало для любого специализированного движка. Целевые латентности: suggest **p95 < 100 мс**, полный поиск **p95 < 200 мс** (end-to-end через Django).

| Критерий | PostgreSQL FTS + pg_trgm | **Meilisearch** | Elasticsearch / OpenSearch |
|---|---|---|---|
| Опечатки (fuzzy) | только триграммы, качество среднее, тюнинг руками | из коробки (1–2 опечатки, настраивается) | `fuzziness`, настраивается |
| Search-as-you-type | сложно: prefix-`tsquery` + trgm, две ветки логики | из коробки (последнее слово — префикс) | `edge_ngram` / completion suggester, требует настройки |
| Русская морфология | стеммер `russian` из коробки | стемминга нет; компенсируется префиксами и typo-tolerance (§6.3) | лучшая: hunspell/ICU-анализаторы |
| Ранжирование текст+популярность | вручную: `ts_rank * f(popularity)` в SQL | декларативно в `rankingRules` | `function_score`, максимум гибкости |
| Фасеты | `GROUP BY`, нагрузка на основную БД | из коробки | aggregations |
| Эксплуатация | нулевая (это та же БД) | один бинарник без зависимостей, 2 vCPU / 4 ГБ RAM хватает с большим запасом | JVM, 2–4+ ГБ heap на узел, кластер, снапшоты — отдельная работа |
| HA | вместе с PG | в OSS-версии single-node | нативная кластеризация |

**Решение: Meilisearch (v1.x).** Поиск — core-UX музыкального сервиса: пользователь набирает «цой» с телефона с опечаткой и ждёт мгновенный дропдаун. Meilisearch даёт typo-tolerance, префиксный поиск, фасеты и кастомное ранжирование по популярности «из коробки», при этом эксплуатационно это один процесс с LMDB-хранилищем — на порядок дешевле в поддержке, чем OpenSearch, и на порядок лучше по UX, чем PG FTS. Один узел 2 vCPU / 4 ГБ держит >1000 RPS по индексу в миллионы документов — запас от нашего пика больше 10×. Интеграция тривиальна — официальный Python SDK `meilisearch`.

PostgreSQL FTS допустим только как «нулевая итерация», если хочется запуститься вообще без новых компонентов, но переезд неизбежен, поэтому закладываем Meilisearch сразу.

**Триггеры миграции на OpenSearch:**

1. Каталог > **20–30 млн документов** или индекс > **30–50 ГБ** — single-node Meilisearch упирается в перестроение индекса и RAM.
2. Требование **мультинодовой HA** для поиска (SLA, при котором минуты даунтайма поиска недопустимы).
3. **Сложные агрегации** в поисковых сценариях или **learning-to-rank** / персонализированное ранжирование.

Миграция дешёвая по построению: индекс — производная от PostgreSQL (§6.4), меняется только клиент индексации и слой запросов, схема API не меняется.

**Отказоустойчивость на нашем масштабе.** Источник истины — всегда PostgreSQL; индекс полностью восстанавливается переиндексацией за 1–2 минуты. При падении узла поиск деградирует (503 на `/search`; каталог и стриминг не затронуты, из readiness-проверки нод Meilisearch исключён — см. «Django-бекенд и API», §7.13), данные не теряются. Дополнительно — ежедневные дампы Meilisearch (`dumps`) для быстрого рестора.

### 6.2. Схема поисковых индексов

Четыре индекса: `tracks`, `albums`, `artists`, `playlists` (только публичные). Документы — денормализованные проекции строк PostgreSQL; `id` документа = BIGINT PK, тот же, что в REST API.

Документ `tracks`:

```json
{
  "id": 90211,
  "title": "Группа крови",
  "title_translit": "gruppa krovi",
  "artist_names": ["Кино"],
  "artist_aliases": ["kino", "kino band"],
  "album_title": "Группа крови",
  "album_id": 8412,
  "artist_ids": [314],
  "genre": "rock",
  "release_year": 1988,
  "explicit": false,
  "duration_ms": 283000,
  "popularity": 87,
  "cover_url": "https://img.example.com/albums/8412/a1b2c3d4/300.webp"
}
```

Настройки индекса `tracks` (порядок `searchableAttributes` в Meilisearch и есть веса — чем выше, тем важнее):

```json
{
  "searchableAttributes": [
    "title", "title_translit",
    "artist_names", "artist_aliases",
    "album_title"
  ],
  "filterableAttributes": ["genre", "artist_ids", "album_id", "explicit", "release_year"],
  "sortableAttributes": ["popularity", "release_year"],
  "rankingRules": ["words", "typo", "proximity", "attribute", "exactness", "popularity:desc"],
  "typoTolerance": { "minWordSizeForTypos": { "oneTypo": 4, "twoTypos": 8 } }
}
```

Остальные индексы (по той же схеме, перечислены только поисковые поля в порядке весов):

| Индекс | Поисковые поля (по убыванию веса) | Фильтры/сортировки | popularity |
|---|---|---|---|
| `artists` | `name`, `aliases[]` (редакторские + автотранслит) | `genres` | лог-нормализация суммы прослушиваний за 30 дней |
| `albums` | `title`, `title_translit`, `artist_names` | `genre`, `release_year`, `album_type` (album/single/EP) | максимум popularity его треков |
| `playlists` | `title`, `description`, `owner_name` | `tracks_count` | `followers_count` (лог-нормализация; денормализованное поле `playlists.followers_count`, см. «Модель данных») |

Источник `aliases` — поле `artists.aliases text[]` в PostgreSQL (см. «Модель данных», §5.3): самый надёжный канал кросс-алфавитного поиска популярных имён.

### 6.3. Русский и английский: морфология, транслитерация, опечатки, автодополнение

**Морфология.** Meilisearch не стеммирует русский, но на практике это закрывается двумя механизмами: (а) последнее слово запроса всегда ищется **по префиксу** — «групп» находит «группа/группы/группой» (русская морфология суффиксальная, префикс покрывает большинство словоформ); (б) typo-tolerance: «группы крови» матчит «Группа крови» как одну «опечатку» (ы→а). Для не-последних слов запроса этого достаточно в 95%+ музыкальных запросов (они короткие: 1–3 слова). Если по метрикам (доля запросов с 0 результатов, §6.8) морфология станет проблемой — это аргумент в триггер миграции на OpenSearch с анализатором `russian`, а не повод городить лемматизацию поверх Meilisearch.

**Транслитерация.** Три слоя:

1. При индексации для кириллических названий генерируем `title_translit`/`name_translit` (библиотека `iuliia`, схема Wikipedia) — запрос латиницей «gruppa krovi» находит «Группа крови».
2. Обратное направление (кириллицей ищут латинское название: «битлз») — через `aliases` артистов (§6.2) плюс словарь синонимов Meilisearch (`"битлз": ["beatles"]`), пополняемый редакторски по топу нулевых запросов.
3. Fallback в API: если запрос в кириллице дал < 3 результатов — повторяем его транслитерированным (одним `multi-search` это второй подзапрос, +~10 мс).

**Опечатки**: конфиг выше — 1 опечатка от 4 символов, 2 от 8. Ничего писать не нужно.

**Автодополнение (search-as-you-type).** Отдельного suggest-индекса не заводим: Meilisearch и есть instant-search движок. `GET /api/v1/search/suggest` — тот же `multi-search` по `tracks`+`artists` с `limit: 5`, `attributesToRetrieve` только для отрисовки строки дропдауна и `attributesToHighlight` для подсветки. Клиенты: debounce 250 мс + отмена предыдущего запроса (AbortController). Кэшировать результаты в Redis не нужно — латентность Meilisearch на таких запросах 5–20 мс, кэш добавил бы только инвалидационные баги.

### 6.4. Наполнение и обновление индекса из PostgreSQL

Принцип: **PostgreSQL — источник истины, индекс — восстанавливаемая производная, консистентность eventual (секунды)**.

**Инкрементальные обновления — без Django-сигналов.** Правила монолита запрещают межприложенческие сигналы (см. «Django-бекенд и API», §7.1), поэтому индексация запускается **явным вызовом из `services.py` каталога** — единственной точки записи каталога — строго через `transaction.on_commit` (иначе воркер прочитает ещё не закоммиченную строку):

```python
# apps/catalog/services.py — единственная точка записи каталога
def update_track(track: Track, **fields) -> Track:
    with transaction.atomic():
        ...  # запись в PostgreSQL
        transaction.on_commit(
            lambda: sync_search_document.delay("track", track.pk))
    return track
```

```python
# apps/search/tasks.py
@shared_task(autoretry_for=(MeilisearchApiError, ConnectionError),
             retry_backoff=True, retry_backoff_max=600, max_retries=8,
             acks_late=True)
def sync_search_document(entity: str, pk: int):
    obj = REGISTRY[entity].objects.filter(pk=pk).first()
    if obj is None or (entity == "playlist" and not obj.is_public):
        index_for(entity).delete_document(pk)      # удалили или скрыли
    else:
        index_for(entity).add_documents([serialize(obj)])  # upsert
```

Задача идемпотентна (upsert по `id`), очередь — отдельная Celery-очередь `search_index` c 1–2 воркерами. Плейлист при переключении `is_public=False` удаляется из индекса этим же кодом. Изменение имени артиста каскадно переиндексирует его треки/альбомы (задача `resync_artist_cascade`, батчами по 1000).

**Полная переиндексация без даунтайма.** Management-команда `manage.py search_reindex --entity tracks`: читает PG чанками по 5 000 через `.iterator()` c `select_related`/`prefetch_related`, пишет в новый индекс `tracks_20260803T1200`, по завершении атомарно меняет местами через нативный `swapIndexes` Meilisearch. 100 тыс. треков ≈ 1–2 минуты. Запускается при изменении схемы документа/настроек индекса и при расхождениях.

**Контроль консистентности.** Ночная Celery-beat задача сравнивает `COUNT(*)` в PG и `numberOfDocuments` в Meilisearch по каждой сущности + сверяет случайную выборку 1 000 документов по `updated_at`. Расхождение > 0,1% → алерт и автоматический запуск переиндексации сущности.

**Обновление популярности.** Ночью, после пересчёта `popularity` (§6.5): частичный bulk-апдейт документов (только `{id, popularity}`) чанками по 10 000 — Meilisearch поддерживает partial update, тела документов не перезаливаются.

### 6.5. Ранжирование результатов

Формула разбита на два слоя.

**Текстовая релевантность** — стандартный порядок правил Meilisearch: `words` (сколько слов запроса найдено) → `typo` (меньше опечаток — выше) → `proximity` (слова рядом) → `attribute` (совпадение в `title` дороже, чем в `album_title`) → `exactness` (точное слово дороже префикса).

**Популярность** — последнее правило `popularity:desc`: среди текстово-равных побеждает популярный трек (запрос «smells» должен поднять Nirvana, а не одноимённый трек безвестной группы).

`popularity` — целое 0–100 (`tracks.popularity SMALLINT`, единый тип в PG и индексе), пересчитывается ночным Celery-beat из `track_stats_daily` (см. «Модель данных», §5.6):

```
plays_30d  = сумма прослушиваний трека за 30 дней (с половинным весом дней 15–30)
popularity = round(100 * ln(1 + plays_30d) / ln(1 + max_plays_30d_по_каталогу))
```

Логарифм сглаживает голову распределения; скользящее окно 30 дней даёт естественное затухание старых хитов. Значение используется и в чартах, и в фолбэках рекомендаций.

### 6.6. API поиска

Единый эндпоинт (DRF, все клиенты ходят сюда):

```
GET /api/v1/search?q=цой&type=track,album,artist,playlist&limit=10&offset=0&filter[genre]=rock
```

- `q` — обязателен, 1–100 символов (пустой — 400); `type` — CSV, по умолчанию все четыре; `limit` ≤ 50 на тип.
- Реализация: один HTTP-вызов `POST /multi-search` в Meilisearch с подзапросом на каждый тип (+ транслит-fallback из §6.3) — параллельно внутри движка, суммарно ~20–40 мс.
- Фасет по типу сущности реализован секциями ответа; внутри треков доступны фильтры-фасеты `genre`, `release_year`, `explicit` (`facets: ["genre"]` → `facetDistribution` в ответе).
- Запрос без результатов возвращает 200 с пустыми секциями (и логируется, §6.8).

```json
{
  "query": "цой",
  "tracks":    { "total": 214, "items": [ { "id": 90211, "title": "...", "artists": [...], "popularity": 87, "highlight": "..." } ] },
  "artists":   { "total": 3,   "items": [ ... ] },
  "albums":    { "total": 12,  "items": [ ... ] },
  "playlists": { "total": 41,  "items": [ ... ] },
  "facets":    { "tracks": { "genre": { "rock": 190, "post-punk": 24 } } }
}
```

Дополнительно:

```
GET /api/v1/search/suggest?q=цо        # топ-5 треков+артистов, лёгкий payload, для дропдауна
```

**Rate limit — единый: 30 запросов/мин на пользователя, общий счётчик для `search` и `suggest`** (один DRF-scope, счётчики в redis-cache). При debounce 250 мс на клиентах этого достаточно для живого набора текста, а скрейперов останавливает.

### 6.7. Обнаружение контента (discovery)

MVP-«главная» собирается из дешёвых блоков без ML:

**Чарты.** Celery-beat каждые 6 часов агрегирует `track_stats_daily` → топ-100 глобально и топ-50 по каждому жанру за окна `day` и `week` → кладёт готовые списки id в Redis (`chart:global:week` — ZSET по plays, TTL 7 ч). Эндпоинт читает Redis и догружает карточки треков из PG (с кэшем):

```
GET /api/v1/discover/charts?scope=global|genre:rock&period=day|week&limit=50
```

**Новинки.** Просто запрос в PG: `albums WHERE release_date >= now() - interval '30 days' ORDER BY release_date DESC, popularity DESC` + индекс по `release_date`; кэш 15 минут.

```
GET /api/v1/discover/new-releases?limit=20
```

**Простые рекомендации (старт).** Без ML: берём топ-3 жанра пользователя по его истории за 30 дней (один GROUP BY по `play_history`, кэш на сутки) и отдаём блоки «Больше из {жанр}» — популярные треки жанра, которые пользователь ещё не слушал (`NOT IN` по последним N прослушанным). Холодный старт (нет истории) → глобальный чарт + новинки.

```
GET /api/v1/discover/recommendations        # массив блоков: {reason: "genre:rock", items: [...]}
```

**Путь к collaborative filtering.** Когда матрица взаимодействий станет достаточно плотной (ориентир: **>30–50 тыс. MAU и в среднем >20 уникальных прослушанных треков на активного пользователя**): ночной batch-джоб (та же Celery, отдельная тяжёлая очередь) обучает implicit ALS (библиотека `implicit`, матрица user×track, confidence = `ln(1+plays)`; наш масштаб на 64 факторах считается на одной машине с 16 ГБ за десятки минут). Результат — топ-100 треков на пользователя в таблицу `user_recommendations (user_id, track_id, score, generated_at)` c подменой целиком; тот же ALS даёт item-to-item похожесть для блока «Похожие треки». API-контракт `/discover/recommendations` при этом **не меняется** — меняется только наполнение блоков, клиенты ничего не замечают. Отдельный сервис рекомендаций не нужен до масштабов, когда batch перестанет влезать в ночь.

### 6.8. История поисковых запросов

Назначение: UX (подсказки «вы искали»), продуктовая аналитика (нулевые запросы → пополнение словаря синонимов §6.3), сырьё для будущей персонализации подсказок. Таблица `search_history` — одна, DDL в «Модели данных», §5.7 (поле `result_count` и частичный индекс по нулевым запросам — обязательны, на них построен отчёт).

- Пишем **только финализированные** запросы (не каждый keystroke): событие фиксируется при клике по результату или при явном «выполнить поиск»; клиент присылает его отдельным вызовом `POST /api/v1/search/history {query, result_count, clicked_type, clicked_id}` (`result_count` — из ответа поиска: на нём построены отчёт по нулевым запросам и частичный индекс `ix_search_history_zero`). Вставка — fire-and-forget Celery-задачей, чтобы не добавлять латентность поиску.
- `GET /api/v1/search/history` — последние 20 уникальных запросов; `DELETE /api/v1/search/history` и `DELETE /api/v1/search/history/{id}` — очистка (privacy-требование).
- Ретенция 90 дней: ночная задача удаляет старые строки батчами.
- Еженедельный отчёт по `result_count = 0` — основной источник пополнения синонимов и алиасов артистов.

---

## 7. Django-бекенд и API

### 7.1. Модульный монолит: структура проекта

Один деплоймент, одна кодовая база, один PostgreSQL — но жёсткие внутренние границы между модулями. При пике < 300 RPS (см. «Допущения») монолит на Django закрывает нагрузку с большим запасом, а границы между приложениями дают дешёвый путь к выносу модуля в отдельный сервис, если это когда-нибудь понадобится.

```
myspotify/
├── config/
│   ├── settings/          # base.py / dev.py / prod.py (django-environ)
│   ├── urls.py            # только include() приложений + /api/v1/ префикс
│   ├── wsgi.py
│   └── celery.py
├── apps/
│   ├── core/              # базовые модели (TimeStampedModel), пагинация, exception handler, healthcheck, request-id middleware
│   ├── users/             # регистрация, JWT, профиль, устройства
│   ├── catalog/           # артисты, альбомы, треки, жанры, медиа-ассеты, presigned-загрузка
│   ├── playlists/         # плейлисты и их треки
│   ├── interactions/      # лайки, история прослушиваний, офлайн-загрузки
│   ├── streaming/         # выдача подписанных URL
│   ├── search/            # индексация и поисковый эндпоинт
│   └── analytics/         # агрегация счётчиков, beat-задачи, чарты
└── manage.py
```

**Правила зависимостей** — строго однонаправленные, «сверху вниз»:

```
analytics ──▶ interactions, catalog
search ─────▶ catalog, playlists, users   (индексирует и публичные плейлисты)
streaming ──▶ catalog, interactions
playlists ──▶ catalog, users
interactions ▶ catalog, users
catalog ────▶ core   (FK на пользователя — строкой "users.User", без импорта)
users ──────▶ core
core ───────▶ ничего
```

Три конвенции, которые делают монолит модульным, а не «большим шаром грязи»:

1. **Межмодульное общение — только через `services.py`** каждого приложения (его публичный API). Прямой импорт чужих `models.py` запрещён; FK между приложениями — строковыми ссылками (`"catalog.Track"`).
2. **Никаких Django-сигналов между приложениями** — только явные вызовы сервисов или постановка Celery-задачи (именно так устроена индексация поиска, §6.4, и инвалидация кэшей). Сигналы остаются для внутримодульных мелочей.
3. **Слои внутри приложения**: `views.py` (тонкие DRF-вьюхи) → `services.py` (запись/бизнес-логика) → `selectors.py` (чтение) → `models.py`. Сериализаторы без логики.

Правила закрепляются инструментально — import-linter в CI:

```ini
[importlinter]
root_package = apps

[importlinter:contract:layers]
name = Слои монолита
type = layers
layers =
    analytics
    search | streaming
    playlists | interactions
    catalog
    users
    core
```

Владение таблицами: `catalog` — треки и медиа-ассеты, `interactions` — лайки, история и офлайн, `playlists` — плейлисты, `users` — пользователи и устройства; `streaming` своих таблиц не имеет. Схемы — в «Модели данных».

### 7.2. API: Django REST Framework

- **Версионирование в пути**: `/api/v1/...` (`URLPathVersioning`). Аддитивные изменения (новые поля, новые эндпоинты) не поднимают версию; breaking-изменения — только `/api/v2/` рядом со старой, окно депрекации 6 месяцев (мобильные клиенты обновляются медленно).
- **Идентификаторы в URL и телах — целые BIGINT id каталога** (обоснование — «Модель данных», §5.1). Те же id — в поисковых документах, клиент склеивает поиск с каталогом напрямую. Единственный UUID наружу — `public_id` пользователя.
- Все ответы — `application/json`; snake_case в полях (генерация клиентов из OpenAPI снимает вопрос конвенций на мобильных).

Группы эндпоинтов:

| Группа | Эндпоинты | Доступ |
|---|---|---|
| Auth | `POST /auth/register`, `POST /auth/token`, `POST /auth/token/refresh`, `POST /auth/logout` | anon / refresh |
| Профиль | `GET/PATCH /me` | user |
| Каталог | `GET /artists/{id}`, `GET /artists/{id}/albums`, `GET /albums/{id}` (с треками), `GET /tracks/{id}`, `GET /genres` | anon (каталог публичный) |
| Поиск | `GET /search`, `GET /search/suggest`, `GET/POST/DELETE /search/history` | anon (история — user) |
| Стрим | `POST /tracks/{id}/stream` | user |
| Плейлисты | `GET/POST /playlists`, `GET/PATCH/DELETE /playlists/{id}`, `POST /playlists/{id}/tracks`, `PATCH/DELETE /playlists/{id}/tracks/{entry_id}` | user (владелец) |
| Лайки | `PUT/DELETE /me/likes/tracks/{track_id}` (идемпотентно), `GET /me/likes/tracks` | user |
| История | `GET /me/history`, `POST /me/playback-events` (батч) | user |
| Офлайн | `POST /offline/sync` | user |
| Discovery | `GET /discover/charts`, `GET /discover/new-releases`, `GET /discover/recommendations` | anon / user |
| Загрузка | `POST /admin/tracks/{id}/audio/upload-init`, `POST /admin/tracks/{id}/audio/upload-complete`, `GET /admin/tracks/{id}/audio/status` | staff |
| Служебные | `GET /healthz`, `GET /readyz`, `GET /api/v1/schema/` | — |

Строки плейлиста адресуются по суррогатному `entry_id` (`playlist_tracks.id`), потому что дубликаты трека в плейлисте разрешены и `track_id` неоднозначен; `track_id` фигурирует только в теле `POST` при добавлении (см. «Модель данных», §5.4).

### 7.3. Примеры запросов/ответов

**Логин:**

```http
POST /api/v1/auth/token
{"email": "user@example.com", "password": "•••"}

200 OK
{"access": "eyJhbGciOiJIUzI1NiJ9...", "refresh": "eyJhbGci...", "access_expires_in": 900}
```

**Альбом с треками:**

```http
GET /api/v1/albums/8412

200 OK
{
  "id": 8412,
  "title": "Группа крови",
  "artist": {"id": 314, "name": "Кино"},
  "release_date": "1988-01-01",
  "album_type": "album",
  "cover": {
    "small":  "https://img.example.com/albums/8412/a1b2c3d4/64.webp",
    "medium": "https://img.example.com/albums/8412/a1b2c3d4/300.webp",
    "large":  "https://img.example.com/albums/8412/a1b2c3d4/640.webp"
  },
  "tracks": [
    {"id": 90211, "title": "Группа крови", "duration_ms": 283000, "track_number": 1, "is_liked": false}
  ]
}
```

URL обложек собираются из `cover_key` по раскладке бакета `images` (см. «Хранение и доставка аудио», §4.9); `is_liked` подмешивается одним `EXISTS`-подзапросом при аутентифицированном запросе — без N+1.

**Поиск** (обслуживается Meilisearch, см. «Поиск и обнаружение контента»):

```http
GET /api/v1/search?q=кино&type=track,artist&limit=10

200 OK
{"artists": {"total": 1, "items": [{"id": 314, "name": "Кино", ...}]}, "tracks": {...}}
```

**Добавление трека в плейлист:**

```http
POST /api/v1/playlists/512/tracks
{"track_id": 90211, "position": 4}

201 Created
{"entry_id": 70443, "track_id": 90211, "position": 4}
```

Ответ содержит `entry_id` — им клиент адресует строку при перестановке (`PATCH /playlists/512/tracks/70443 {"position": 2}`) и удалении (`DELETE /playlists/512/tracks/70443`).

**Лайк** — `PUT /api/v1/me/likes/tracks/90211` → `204` (повторный `PUT` — тоже `204`, идемпотентно).

### 7.4. Аутентификация: JWT access + refresh для всех клиентов

Один механизм для веба, Android и iOS — **`djangorestframework-simplejwt`**. Серверных сессий нет ни для одного клиента.

**Почему не session-cookie.** (1) Нативным мобильным клиентам пришлось бы эмулировать cookie-jar и CSRF-танцы — Bearer-заголовок естественнее. (2) Сессии требуют похода в хранилище на каждый запрос; JWT access проверяется подписью локально, без I/O. (3) Один stateless-механизм на три клиента вместо двух — меньше кода и поверхности ошибок.

```python
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,          # каждый refresh выдаёт новую пару
    "BLACKLIST_AFTER_ROTATION": True,       # старый refresh мгновенно недействителен
    "ALGORITHM": "HS256",                   # RS256 — когда токен начнут проверять другие сервисы
    "AUTH_HEADER_TYPES": ("Bearer",),
}
```

- **Access 15 минут** — компромисс: кража токена ограничена по времени, но refresh-запрос лишь раз в четверть часа. **Refresh 30 дней** — мобильное приложение не разлогинивается неделями; refresh привязан к устройству (`user_devices`), отзыв устройства инвалидирует его токен.
- **Ротация + blacklist**: отозванные refresh хранятся в **PostgreSQL** (приложение `token_blacklist` из simplejwt) — это единственное хранилище отзыва, никаких denylist в Redis. Повторное использование уже ротированного refresh — признак кражи, вся цепочка инвалидируется. `POST /auth/logout` кладёт refresh в blacklist. Чистка протухших записей — ночная Celery-задача.

Хранение на клиентах:

| Клиент | access | refresh |
|---|---|---|
| Web (React) | только в памяти JS (не в localStorage — XSS) | httpOnly Secure-cookie, `Path=/api/v1/auth/`; эндпоинт refresh читает cookie для веба и тело запроса для мобильных |
| iOS | память | Keychain |
| Android | память | EncryptedSharedPreferences (Jetpack Security) |

### 7.5. Эндпоинт стрима: единый контракт

Ключевой инвариант: **ни один байт аудио не проходит через Django**. Бекенд — только control plane. Это канонический контракт; механика подписи и Worker — в «Хранении и доставке аудио», §4.7.

```http
POST /api/v1/tracks/90211/stream
Authorization: Bearer eyJ...
{"quality": "normal", "intent": "stream"}   # оба поля опциональны

200 OK
{
  "track_id": 90211,
  "url": "https://media.example.com/0b0f4a.../v1/aac_160.m4a?e=1785808800&t=ab12cd...",
  "quality": "normal",
  "bitrate_kbps": 160,
  "expires_at": "2026-08-04T02:00:00Z"
}
```

- `quality` по умолчанию — `preferred_quality` профиля, ограниченная сверху `plans.max_quality` тарифа; `intent: download` помечает офлайн-скачивание (см. §4.11).
- **TTL подписи — 6 часов** (единый, здесь и в §4.7).
- Внутри (~5 мс, один запрос к БД): проверка JWT (без I/O) → `audio_status == 'ready'` и потолок качества → `streaming.services.build_signed_url(track, quality)` — чистая криптография на ключе, ни R2, ни CDN не вызываются.
- **Выдача stream-URL ничего не пишет в историю прослушиваний.** Выданный URL — не прослушивание; учёт идёт единственным каналом `POST /me/playback-events` (§7.6).
- **POST, а не GET** — у запроса есть сайд-эффект (генерация короткоживущей подписи), и это исключает кэширование ссылки промежуточными слоями.

### 7.6. Учёт прослушиваний: батч playback-событий

Единственный канал учёта — клиентские события, присылаемые батчем раз в 60 секунд (и при сворачивании приложения; тот же интервал заложен в оценку RPS в «Допущениях»):

```http
POST /api/v1/me/playback-events
Authorization: Bearer eyJ...
{
  "events": [
    {"track_id": 90211, "started_at": "2026-08-03T19:12:44Z", "ms_played": 214000,
     "source": "playlist", "source_id": 512, "device_fingerprint": "3f1c..."}
  ]
}

202 Accepted
```

Обработка: вьюха валидирует батч и ставит Celery-задачу (очередь `default`) — вставка в `play_history` и инкременты не блокируют ответ. «Прослушивание» засчитывается по правилу **«30 секунд или 50% трека»**; зачтённые события проходят дедупликацию (`play:dedup`, SET NX 30 с) и инкрементируют Redis-буфер `plays:buf` (HINCRBY), который beat-задача раз в 60 секунд сбрасывает в `track_play_counts`. Полный поток данных и DDL — «Модель данных», §5.6.

### 7.7. Celery + Redis: фоновые задачи

Брокер и result backend — инстанс redis-queue (`noeviction`, см. «Модель данных», §5.8). Очереди — чтобы тяжёлое не блокировало лёгкое:

| Очередь | Где | Воркеры | Задачи |
|---|---|---|---|
| `default` | services-1 | 2 × concurrency 8 | письма (подтверждение почты, сброс пароля), запись playback-событий и search_history, офлайн-ревалидация |
| `search_index` | services-1 | 1–2 | инкрементальная индексация Meilisearch (§6.4) |
| `analytics` | services-1 | 1 × concurrency 4 | сброс Redis-буфера счётчиков, чарты, агрегаты, пересчёты |
| `media` | **media-1 (отдельная нода)** | concurrency = числу ядер | ffmpeg-транскодирование (CPU-bound), ресайз обложек |

Очередь `media` живёт на отдельной (можно временной) ноде: ffmpeg не должен соседствовать ни с Meilisearch, ни с остальными очередями — CPU-bound транскод задушит их латентность. На период первичного импорта каталога нода временно масштабируется до 8 ядер (см. §4.4 и «Инфраструктуру»).

Celery beat:

- **каждые 60 секунд** — сброс `plays:buf` → `track_play_counts` (единственная агрегация счётчиков, см. «Модель данных», §5.6);
- **каждые 6 часов** — пересборка чартов → Redis (§6.7);
- **ежечасно** — досылка недоиндексированных документов в поиск (сверка по `updated_at`);
- **еженощно** — агрегация `track_stats_daily`; пересчёт `tracks.popularity` + partial-апдейт индекса; пересчёт `playlists.followers_count`; сверка консистентности поиска; создание партиций `play_history` на 2 месяца вперёд и DROP партиций старше 12 месяцев; чистка `search_history` старше 90 дней; чистка протухших записей `token_blacklist`.

Правило: всё, что дольше ~100 мс или ходит во внешние системы (SMTP, ffmpeg, Meilisearch, R2) — только через Celery. Во вьюхах — исключительно PostgreSQL и Redis.

### 7.8. WebSocket: на старте не нужны

В MVP нет ни одной фичи, требующей server push: плеер полностью клиентский (получил подписанный URL — дальше общается с CDN), плейлисты персональные, чатов и совместного прослушивания нет. Django Channels притащил бы ASGI-сервер, channel layer в Redis, усложнение деплоя и отладки — ради нуля пользовательской ценности.

Единственный кандидат — статус транскодинга для контент-менеджера: решается поллингом `GET /admin/tracks/{id}/audio/status` раз в 5 секунд (загрузок — десятки в день, это ничто).

Когда пересмотреть: синхронизация плеера между устройствами (аналог Spotify Connect), совместное редактирование плейлистов в реальном времени. До тех пор проект остаётся чистым WSGI (gunicorn с синхронными воркерами), что проще и в деплое, и в профилировании.

### 7.9. Rate limiting и троттлинг

Два уровня:

1. **nginx (edge)** — грубый потолок от абьюза до Python: `limit_req` 20 r/s с burst 40 на IP для `/api/`, отдельно 5 r/m на `/api/v1/auth/token` (брутфорс).
2. **DRF ScopedRateThrottle** — бизнес-лимиты по пользователю, счётчики в redis-cache:

```python
REST_FRAMEWORK = {
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/min",       # анонимный просмотр каталога
        "user": "1000/hour",     # общий потолок на пользователя
        "auth": "10/min",        # login/register/refresh, по IP
        "search": "30/min",      # ЕДИНЫЙ scope для /search и /search/suggest
        "stream": "120/hour",    # физически слушать можно ~20 треков/час; 120 ловит скрейперов,
                                 # не мешая перемоткам и офлайн-скачиванию альбомов
        "upload": "50/day",      # presigned-выдача, staff
    },
}
```

Ответ при превышении — стандартный `429` c заголовком `Retry-After`; клиенты обязаны его уважать (зашивается в генерируемый SDK-слой).

### 7.10. Пагинация: cursor по умолчанию

**`CursorPagination` (page_size = 50)** — для всех пользовательских лент: история, лайки, списки альбомов/треков артиста. Причины: (1) стабильность — при вставке новых записей страницы не «съезжают» и элементы не дублируются, критично для бесконечной прокрутки в мобильных клиентах; (2) `WHERE (created_at, id) < (…) LIMIT 50` по индексу — O(page) на любой глубине, тогда как `OFFSET 10000` сканирует и отбрасывает 10 000 строк.

```json
{
  "next": "https://api.example.com/api/v1/me/history?cursor=cD0yMDI2LTA4...",
  "previous": null,
  "results": [ ... ]
}
```

Cursor — непрозрачная строка: клиент просто ходит по `next`. Offset-пагинация остаётся в двух местах: Django Admin (там нужны номера страниц) и поиск (глубину листания ограничиваем 500 результатами — дальше никто не ходит, а движок сам управляет своим окном).

### 7.11. Django Admin как админка каталога

На старте контент-операции (завести артиста, собрать альбом, поправить метаданные, снять трек с публикации) полностью закрывает Django Admin — ноль затрат на отдельный фронтенд:

- `ArtistAdmin`, `AlbumAdmin` с `TrackInline` (треки редактируются на странице альбома), `list_select_related`, `search_fields`, `autocomplete_fields` для FK;
- статусы аудио (`uploaded / processing / ready / failed`) — readonly-поля, лог ошибки транскодинга виден прямо в карточке;
- admin actions: «отправить на транскодинг повторно», «переиндексировать в поиске», «снять с публикации»;
- безопасность: админка на отдельном пути с ограничением по IP/VPN на nginx, staff-аккаунты с обязательным 2FA (`django-otp`).

Переход на собственную React-админку поверх того же API (permission `IsAdminUser`) оправдан, только когда появятся выделенные роли контент-операторов с воркфлоу (модерация, очереди задач) — не раньше.

### 7.12. Загрузка треков и обложек

Флоу загрузки один и описан в «Хранении и доставке аудио», §4.4: `upload-init` (presigned PUT в бакет `masters`, лимит 200 МБ, TTL 1 ч) → PUT напрямую в бакет → `upload-complete` (HEAD-проверка, постановка транскод-цепочки в очередь `media`) → поллинг `status`. Файлы не проходят через gunicorn ни на одном шаге. Тот же механизм presigned PUT — для обложек (лимит 10 МБ, ресайз в Celery, §4.8).

### 7.13. Ошибки, healthcheck, OpenAPI

**Единый формат ошибок** — кастомный `EXCEPTION_HANDLER`, машиночитаемый `code` (клиенты матчатся по нему, не по тексту), `request_id` для склейки с логами:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Некорректные данные запроса.",
    "details": {"email": ["Пользователь с таким email уже существует."]},
    "request_id": "01J9WXQ8ZK3F..."
  }
}
```

`request_id` генерируется middleware (`X-Request-ID` принимается от nginx или создаётся), пишется в каждую строку лога (structlog, JSON-формат) и возвращается в заголовке ответа. Неожиданные исключения → `500` с тем же конвертом без внутренних деталей + событие в Sentry.

**Healthcheck** — два эндпоинта с разной семантикой, мимо DRF-троттлинга:

- `GET /healthz` — **liveness**: константный `200`, без обращений к зависимостям. Используется только рестарт-политикой оркестратора («жив ли процесс»).
- `GET /readyz` — **readiness**: `SELECT 1` в PostgreSQL + `PING` в оба Redis; при отказе — `503`. **Именно сюда ходят балансировщик и внешний uptime-мониторинг** — нода без БД должна выйти из ротации. Meilisearch в readiness не включаем: его деградация не повод убирать ноду (поиск отвечает 503 только на своём эндпоинте).

**OpenAPI — drf-spectacular** как единственный источник контракта:

- схема генерируется из кода (сериализаторы + `@extend_schema`), доступна на `/api/v1/schema/`; Swagger UI — только в dev/staging;
- клиенты: `openapi-generator-cli -g kotlin` (Android), `-g swift5` (iOS), `orval`/`openapi-typescript` (React) — типизированные SDK из одной схемы, ручной дрейф контракта между тремя клиентами исключён;
- в CI схема коммитится рядом с кодом; job сравнивает её с предыдущей (`oasdiff`) и фейлит PR при breaking change без поднятия версии API.

---

## 8. Инфраструктура, развёртывание, эксплуатация

### 8.1. Стартовая топология

Нагрузка — из «Допущений»: пик ~120–150 RPS на API (закладываем < 300), аудио целиком на CDN. Это нагрузка для 2–3 машин плюс managed-сервисы; никакого Kubernetes и микросервисов.

```
Клиенты (React / Android / iOS)
        │ HTTPS
        ▼
  Load Balancer (health check → /readyz)
        │
   ┌────┴────┐
   ▼         ▼
 app-1     app-2          ← nginx + gunicorn (синхронные WSGI-воркеры), Django, stateless
   │         │
   ├──► PostgreSQL 16 (managed, private network)
   ├──► redis-cache (2 ГБ, allkeys-lru)
   ├──► redis-queue (1 ГБ, noeviction: брокер Celery + счётчики)
   ├──► Meilisearch (VM services-1)
   ├──► Celery workers (services-1: default / search_index / analytics)
   └──► Celery workers (media-1: очередь media, ffmpeg)

Cloudflare R2 + CDN + Worker  ← аудио (.m4a) и обложки, мимо Django
```

Внутри app-ноды: `nginx` (TLS-терминация, если без LB; gzip/brotli, rate limiting) → `gunicorn` с **синхронными WSGI-воркерами**, 4–6 воркеров на ноду 2 vCPU. Async/uvicorn-воркеры не используются: вьюхи полностью синхронные, медленных мобильных клиентов буферизует nginx, а ASGI только усложнил бы отладку. Рост — добавлением таких же нод за балансировщиком.

Смета (DigitalOcean как ориентир; Hetzner в 2–3 раза дешевле, но без managed PostgreSQL):

| Компонент | Размер | Цена/мес |
|---|---|---|
| app-1, app-2 | 2 vCPU / 4 ГБ каждая | 2 × $24 |
| Managed PostgreSQL | 2 vCPU / 4 ГБ, ежедневные бэкапы + PITR | $60 |
| Managed Redis (redis-cache) | 2 ГБ | $30 |
| Managed Redis (redis-queue) | 1 ГБ | $15 |
| services-1 (Meilisearch + Celery default/search_index/analytics) | 2 vCPU / 4 ГБ | $24 |
| media-1 (Celery media, ffmpeg) | 2 vCPU / 4 ГБ | $24 |
| Load Balancer | managed | $12 |
| R2: хранение 4,6 ТБ + Workers | | ~$74 |
| Backblaze B2: реплика бакета masters (3 ТБ) | | ~$18 |
| Sentry (Team) + Grafana Cloud (free tier) | | ~$26 |
| **Итого** | | **~$330/мес** |

Разовое: временный апгрейд media-1 до 8 ядер на ~2 суток первичного импорта каталога (~$25). Egress аудио в смете отсутствует принципиально — у R2 он нулевой (см. §4.5).

Ключевое требование — **PostgreSQL managed**: автоматические бэкапы, PITR, обновления, метрики из коробки. Это единственный stateful-компонент, потеря которого фатальна, — не экономьте на нём $30/мес. Если хостинг в РФ — те же роли закрывает Yandex Cloud (Managed PostgreSQL + Object Storage + CDN); вопрос локализации ПДн — в последнем разделе.

### 8.2. Docker Compose сейчас, Kubernetes — потом (нескоро)

Один и тот же `docker-compose.yml` (с override-файлами `compose.dev.yml` / `compose.prod.yml`) — и для локальной разработки, и для прода. В dev поднимаются все зависимости (postgres, оба redis, meilisearch, minio как замена R2); в проде — только контейнеры `web`, `celery-worker`, `celery-beat`, `nginx`, остальное — внешние сервисы.

**Когда пора в Kubernetes** — когда выполняется хотя бы два из:

- больше ~10–15 виртуалок и ручной rolling-деплой стал болью;
- несколько команд деплоят независимо;
- нужен реальный автоскейлинг (суточные пики в разы, транскодирование огромных объёмов).

При тысячах пользователей ни одно условие не выполняется. Реалистично K8s понадобится в районе 50–100 тыс. одновременных, т.е. после 10–20x роста. Преждевременный переезд — это +1 инженер только на обслуживание кластера.

### 8.3. Горизонтальное масштабирование: stateless Django

Правила, которые делают app-ноды взаимозаменяемыми (нарушение любого — блокер масштабирования):

- **никакого серверного состояния сессий** — JWT для всех клиентов (см. §7.4); в Redis сессий нет;
- **никаких файлов на локальном диске** — всё в R2 через `django-storages`/boto3;
- **никакого локального кэша** — только redis-cache;
- фоновая работа — только через Celery, не через потоки в вебе.

Тогда рост — механический: добавить app-ноду, включить в балансировщик (round robin + health check на `/readyz`). До ~10 нод никаких изменений архитектуры не требуется.

### 8.4. Кэширование по слоям

| Слой | Что кэширует | TTL / механика |
|---|---|---|
| **CDN** | `.m4a`-файлы и обложки | Контент иммутабелен (в ключах `v{rev}` и `{sha8}`): `Cache-Control: public, max-age=31536000, immutable`. Ожидаемый hit ratio **70–85%** (Ципф: ~10% треков дают ~80% трафика) |
| **HTTP-кэш клиента** | Каталог: `GET /api/v1/albums/{id}`, `GET /api/v1/artists/{id}` | `ETag` (по `updated_at` записи) + `Cache-Control: public, max-age=300`. Клиенты шлют `If-None-Match`, получают дешёвый `304` |
| **Redis (redis-cache)** | Горячие агрегаты: альбом с треками, страница артиста, главная, чарты; лайк-сеты `likes:u:{id}` (id для отрисовки сердечек в выдаче) | `cache.get_or_set()` с TTL 60–900 с; инвалидация явным вызовом из `services.py` при записи. Ключи с версией схемы: `cache:v1:album:{id}` |
| **Не кэшируем** | Плейлисты пользователя, история, ответ `GET /me/likes/tracks`, результаты поиска | Персонализированное и дешёвое по БД — кэш тут даёт баги, а не скорость; Meilisearch сам отвечает за 5–20 мс (§6.3) |

Важно: при ~150 RPS кэширование — это не про выживание, а про p95 и про счёт за БД. Не кэшируйте превентивно всё подряд — только то, что показал профайлер (django-silk в staging).

### 8.5. Мониторинг, алертинг, логи

- **Sentry** — ошибки и APM-трейсы Django/Celery (`sentry-sdk`, traces_sample_rate ~0.1). Первый по важности инструмент: покажет медленные SQL и N+1 раньше, чем метрики.
- **Prometheus + Grafana** (или Grafana Cloud free tier, чтобы не держать свой стек): `django-prometheus`, `celery-exporter`, `postgres_exporter` (у managed-провайдера метрики обычно уже есть), `redis_exporter`, `node_exporter`.

Дашборд и алерты — по короткому списку, а не по 200 метрикам:

| Метрика | Алерт |
|---|---|
| p95 латентность API по эндпоинтам | > 500 мс 5 минут подряд |
| Доля 5xx | > 1% |
| Лаг очереди Celery (возраст старейшей задачи) | > 5 мин (для `media` свой порог — > 30 мин) |
| Использование connection pool PostgreSQL | > 80% от `max_connections` |
| Диск на нодах и в БД | > 80% |
| Память redis-queue | > 80% (политика `noeviction`: переполнение = отказ записи задач) |
| CDN hit ratio | **< 60% или резкое падение относительно бейзлайна** (ожидаемый уровень 70–85%, см. §4.6; падение = сломалась иммутабельность URL или кэш-конфигурация) |

Alertmanager → Telegram/Slack. Внешний uptime-чекер (UptimeRobot/Betterstack, бесплатно) — на `/readyz` (не на `/healthz`: константный liveness не заметит отвалившуюся БД), чтобы узнавать о падении не от пользователей.

**Логи**: structured JSON через `structlog` (request_id, user_id, латентность), stdout контейнеров → Grafana Alloy → **Loki**. ELK на этом масштабе избыточен. Retention 14–30 дней.

### 8.6. Бэкапы

**PostgreSQL:**

- **Managed-провайдер**: ежедневный полный бэкап + непрерывная архивация WAL = **PITR на любую секунду за последние 7–14 дней**. Это дефолт — проверить, что включён.
- Дополнительно (защита от «провайдер потерял/забанил аккаунт»): ночной `pg_dump -Fc` в **другое** облако (бакет в другом аккаунте), retention 30 дней. Скрипт — обычный cron на services-1; размер дампа на старте — единицы ГБ.
- Если БД self-hosted (вариант Hetzner): **pgBackRest или WAL-G** в объектное хранилище — базовый бэкап еженощно + непрерывный WAL-архив; голый pg_dump без WAL не даёт PITR.
- **Учения по восстановлению раз в квартал**: развернуть бэкап на пустой VM, прогнать миграции и smoke-тест. Непроверенный бэкап — это не бэкап.
- **Реплика**: на старте не нужна для нагрузки; standby-реплика у managed-провайдера (+60–100% к цене БД) включается, когда даунтайм в 10–15 минут (восстановление из бэкапа) станет бизнес-неприемлемым — обычно это момент появления платных подписок.

**Объектное хранилище** — бэкапится не хуже БД, потому что FLAC-мастера невосстановимы:

- на бакете `masters` включено **версионирование объектов** (защита от случайной перезаписи/удаления, в т.ч. багом пайплайна);
- **периодическая репликация `rclone sync` бакета `masters` во второе хранилище у другого вендора — Backblaze B2** (~$18/мес за 3 ТБ), cron на services-1, еженедельно + сверка контрольных сумм;
- бакет `images` реплицируется туда же (оригиналы обложек — тоже единственные копии, а объём копеечный, ~50 ГБ);
- бакет `audio` **не бэкапится**: транскоды полностью восстановимы из мастеров прогоном пайплайна (§4.4).

### 8.7. CI/CD: GitHub Actions

Pipeline на каждый PR и push в `main`:

```yaml
jobs:
  test:      # ruff + mypy + pytest; postgres:16 и redis:7 как service containers
  build:     # needs: test; только на main
             # docker build → push ghcr.io/org/app:${{ github.sha }} (+ tag latest)
  deploy:    # needs: build; environment: production (ручной approve — опционально)
             # ssh app-1 'docker compose pull && docker compose run --rm web python manage.py migrate'
             # затем поочерёдно: вывести ноду из LB → compose up -d → health check → вернуть в LB
```

Принципы:

- **Миграции — до переключения трафика** и только обратно-совместимые (двухфазные: сначала добавить колонку, задеплоить код, потом удалить старую) — тогда rolling-деплой по нодам даёт zero-downtime без всякой оркестрации.
- Образ собирается **один раз** и промоутится по окружениям; тег — SHA коммита, никакого «пересобрать на сервере».
- Rollback = `docker compose up -d` с предыдущим SHA (одна команда, храним последние N тегов).
- Staging-окружение — одна дешёвая VM ($12–24) с тем же compose-файлом.

### 8.8. Секреты и конфигурация

- Конфигурация — строго через переменные окружения (12-factor), чтение через `django-environ`. Различия окружений — только в env, никаких `settings_prod.py` с копипастой.
- В git — только `.env.example` с именами переменных. Реальные значения: на серверах — файл `/etc/myspotify/.env` (права 600, подключён в compose через `env_file`), в CI — GitHub Actions Secrets / Environments.
- Обязательный минимум секретов: `SECRET_KEY`, `DATABASE_URL`, `REDIS_CACHE_URL`, `REDIS_QUEUE_URL`, DSN Sentry, ключи R2 (отдельный ключ с минимальными правами на конкретный бакет), `MEDIA_SIGNING_KEY` (подпись stream-URL, ротация по процедуре из §4.7), `MEILI_MASTER_KEY`.
- `DEBUG=False` в проде проверяется тестом в CI. Ротация ключей — процедурой в runbook, а не «когда-нибудь».
- Vault/Doppler/SOPS — когда секретов станут десятки и людей с доступом больше трёх; раньше это лишняя движущаяся часть.

---

## 9. Путь масштабирования: что менять при 10x

10x от «Допущений» — это ~100 тыс. DAU и 20–30 тыс. одновременных слушателей. Главное: узкое место архитектуры — не число слушателей. Django участвует в доставке одним лёгким запросом на трек (~5 мс), весь тоннаж несут CDN и R2, которые скейлятся сами и денег за трафик не берут. Порядок действий — по мере появления узких мест, а не всё сразу.

### 9.1. Бекенд и данные

1. **PgBouncer** (transaction pooling) перед PostgreSQL — первый шаг, как только соединений от выросшего числа воркеров станет сотни. У managed-провайдеров включается галочкой.
2. **Read-реплики PostgreSQL** + database router в Django: каталог, discovery, публичные плейлисты — на реплику; всё пишущее и «прочитай-своё-же» — на primary. API к этому готов, если с первого дня не смешивать чтение и запись в одном запросе без нужды.
3. **Больше app-нод** — дёшево и линейно: ноды stateless (§8.3), добавляются за балансировщик без изменений кода.
4. **Автоскейл транскодирования**: очередь `media` и так на отдельной ноде — при массовых загрузках каталога она превращается в пул spot/temporary-инстансов с автоскейлом по длине очереди. Это CPU-bound нагрузка с всплесками, ей нечего делать на общих машинах.
5. **ClickHouse для аналитики** — по триггеру из «Модели данных» §5.6 (~500 млн строк истории или тяжёлые аналитические запросы): полный поток событий уходит через Celery-батчи/Kafka в ClickHouse (чарты, роялти, фичи рекомендаций считаются там), в PostgreSQL остаются последние 90 дней для пользовательского UI.
6. **Kubernetes** — только по критериям §8.2 (реалистично — 50–100 тыс. одновременных). Первый кандидат на вынос в отдельный сервис — транскодирование: единственный модуль с независимым профилем нагрузки.

### 9.2. Аудио и доставка

- **×10 слушателей** — ничего не менять: тот же R2 + CDN (egress по-прежнему $0), добавить Celery-воркеров под приток контента.
- **Opus-транскоды** — экономия ~35% трафика Android/веб: добавляются ступени `opus_64/96/160` рядом с AAC, клиенты объявляют поддержку в запросе `stream`, iOS остаётся на AAC. Мастера уже лежат в `masters` — это только прогон пайплайна.
- **DRM / контракты с мейджор-лейблами** — HLS (fMP4/CMAF) из тех же мастеров + Multi-DRM SaaS (Widevine + FairPlay); меняется формат ответа `stream` (URL манифеста вместо файла) и клиентские плееры, пайплайн дополняется упаковкой сегментов. До контрактов не начинать (§4.7).
- **Lossless-тир** — FLAC уже в `masters`, добавляется только ступень раздачи и потолок тарифа.
- **Гео-распределение** — Cloudflare CDN уже глобален; при жёстких требованиях к latency origin — R2 location hints или репликация во второй регион.

### 9.3. Поиск и рекомендации

- **Meilisearch → OpenSearch** — по триггерам §6.1: каталог > 20–30 млн документов, индекс > 30–50 ГБ, требование мультинодовой HA или learning-to-rank. Миграция дешёвая: индекс — производная PostgreSQL, API не меняется.
- **Collaborative filtering (implicit ALS)** — по триггеру §6.7 (>30–50 тыс. MAU, плотная матрица взаимодействий); контракт `/discover/recommendations` не меняется.

### 9.4. Чего не делать при 10x

Шардировать PostgreSQL (реплики + ClickHouse снимают проблему задолго до предела одной primary на запись), переписывать на микросервисы, менять Django — при вынесенном аудио-трафике монолит спокойно доживает до сотен тысяч пользователей.

---

## 10. За рамками MVP / открытые вопросы

Темы, сознательно не проработанные в этом документе. Для каждой — почему это важно и в какую сторону решать. (Gapless-воспроизведение, бэкап объектного хранилища и миграции партиционированных таблиц уже встроены в основной текст: §4.4, §8.6 и §5.6 соответственно.)

**Платежи и биллинг.** Схема `subscriptions` готова к интеграции (поля `external_customer_id`/`external_sub_id`, статусы `trialing`/`past_due`), но весь платёжный контур не спроектирован: выбор провайдера, вебхуки о списаниях и отказах, dunning-цепочка для `past_due`, проration при смене тарифа. Для цен в рублях обязательна фискализация чеков по 54-ФЗ — это отдельная интеграция (ОФД) или провайдер, который берёт её на себя. Начинать с провайдера, у которого подписки и чеки «из коробки», а не собирать из голых карточных платежей.

**Лицензирование музыки и роялти.** Юридическое ядро музыкального сервиса: без договоров с правообладателями каталог нелегален. Нужны договорная схема (агрегаторы/лейблы/ОКУПы), отчётность по прослушиваниям и расчёт выплат — источником служат `play_history`/`track_stats_daily`, но формат отчётов диктуют контракты. Влияет на архитектуру заранее в одном месте: требования к точности и аудируемости счётчиков прослушиваний выше «продуктовых», занижать ретенцию агрегатов нельзя.

**GDPR/152-ФЗ и удаление аккаунта.** Право на удаление и экспорт данных: каскад должен пройти по партициям `play_history`, `search_history`, поисковому индексу (документы плейлистов содержат `owner_name`) и — организационно — по бэкапам (обычно решается политикой «бэкапы истекают за N дней, из живой базы удалено сразу»). При зарубежных провайдерах (Cloudflare, DigitalOcean) отдельно решается вопрос локализации ПДн россиян по 152-ФЗ — возможно, гибрид с размещением БД в РФ. Согласия, privacy policy и age gate проектируются вместе с регистрацией, а не после.

**Стратегия тестирования.** В CI есть только ruff/mypy/pytest. Не спроектированы: интеграционные тесты пайплайна транскодирования на эталонных файлах (валидация, громкость, gapless-метаданные), contract-тесты трёх клиентов против OpenAPI-схемы (oasdiff ловит breaking changes, но не семантику), нагрузочное тестирование (k6/locust) хотя бы поискового и стримового путей перед запуском.

**Gapless и кроссфейд.** Gapless заложен в пайплайн транскодирования (§4.4). Кроссфейд — чисто клиентская фича (двойной буфер плеера с наложением хвоста и головы треков); бекенд уже отдаёт всё нужное — `duration_ms` и `loudness_lufs` для выравнивания громкости. Требует аккуратной реализации на трёх платформах — планировать как отдельную клиентскую итерацию.

**Enforcement лимита одновременных стримов.** `plans.max_concurrent_streams` объявлен, но механизма нет. Направление: heartbeat активных сессий воспроизведения в Redis (TTL-ключи `session:{user}:{device}`), при старте нового стрима сверх лимита — вытеснение старейшей сессии с push-сигналом клиенту «воспроизведение остановлено на другом устройстве» (как в Spotify). Требует канала server→client (поллинг статуса или будущий WebSocket) — поэтому отложен вместе с ним.

**Ограничения free-тарифа.** Чем free отличается от premium кроме `max_quality`: реклама (аудио-вставки — отдельная подсистема: инвентарь, трекинг, частотность), shuffle-only и лимиты скипов, недоступность офлайна. Каждое ограничение живёт и в API (feature-флаги в `/me`), и в трёх клиентах; экономика тарифа определяет приоритет. До появления платежей все пользователи фактически premium — это осознанное упрощение MVP.

**Email-инфраструктура.** Верификация почты и сброс пароля упомянуты как Celery-задачи, но не выбран SMTP-провайдер (Postmark/SES/Mailgun — по доставляемости в целевые почтовики, включая российские), нет шаблонов, DKIM/SPF/DMARC, обработки bounce/complaint (вебхук → пометка адреса недоставляемым). Объём мизерный, но без этого регистрация ломается о спам-фильтры.

**Модерация и takedown.** Процедура снятия трека по требованию правообладателя: admin action «снять с публикации» есть, но полный цикл — нет: немедленное исключение из выдачи и поиска, отзыв офлайн-загрузок через `offline/sync` (флаг `revoked_at` в `offline_downloads` уже заложен), журнал требований и сроков реакции. Плюс возрастные ограничения explicit-контента: поле `is_explicit` есть, фильтрация по возрасту из профиля — нет.
