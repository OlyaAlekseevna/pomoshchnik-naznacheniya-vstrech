# Google Calendar Meeting Bot (MVP)

## Что уже готово на этапе 1

- Базовый FastAPI backend.
- Подключен каркас aiogram.
- Конфигурация через переменные окружения.
- Docker Compose для `app + PostgreSQL + Redis`.
- Endpoint `GET /health`.
- Базовое структурированное логирование.
- Базовые проверки: `ruff` и `pytest`.
- Alembic-миграции и модель данных Этапа 2.

## Быстрый локальный запуск через Docker Compose

1. Скопируйте пример окружения:

```powershell
Copy-Item .env.example .env
```

2. Запустите сервисы:

```powershell
docker compose up --build
```

3. Проверьте health-check:

```powershell
curl http://localhost:8000/health
```

`/health` возвращает статус по `postgresql`, `redis` и `google_oauth`.

## Локальный запуск без Docker

1. Создайте виртуальное окружение и установите зависимости:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

2. Запустите приложение:

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

3. Для режима без внешних сервисов (только локальная проверка API):

```powershell
$env:APP_SKIP_EXTERNAL_CHECKS = "true"
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Проверки качества

```powershell
ruff check .
pytest
```

## Mini App prototype (dev)

Прототип Mini App подключается как дополнительный канал к существующему боту и не заменяет его.

1. Убедитесь, что включены флаги в `.env`:

```env
MINIAPP_ENABLED=true
MINIAPP_DEV_LOGIN_ENABLED=true
```

2. Запустите приложение (локально или через Docker Compose).

3. Откройте прототип в браузере:

```text
http://localhost:8000/miniapp
```

4. Для входа в прототип используйте `dev login` по Telegram ID.

Полезные маршруты:

1. `GET /miniapp` — UI прототипа.
2. `GET /api/miniapp/health` — health Mini App API.
3. `POST /api/miniapp/auth/dev-login` — быстрый вход в dev.
4. `GET /api/miniapp/notifications` — продуктовый блок уведомлений.
5. `GET /api/miniapp/support` — продуктовый блок поддержки.

### Mini App на том же VPS (prod)

Для публикации Mini App на том же сервере, где работает бот, добавлен отдельный HTTPS-контур (Caddy + Let's Encrypt):

1. `docker-compose.public.yml`
2. `docker/caddy/Caddyfile`
3. инструкция: `docs/miniapp-domain-vps.md`

Коротко:
1. привязать домен к `132.243.23.161`;
2. выставить `MINIAPP_DOMAIN` и `MINIAPP_ENABLED=true` на VPS;
3. запустить deploy с `COMPOSE_FILES=docker-compose.yml:docker-compose.public.yml`.

Графика прототипа:

1. Кастомные иконки: `app/miniapp/static/icons/*`.
2. Иллюстрации ключевых экранов: `app/miniapp/static/illustrations/*`.

## Миграции базы данных

Применить миграции:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://calendar_user:calendar_password@localhost:5432/calendar_bot"
alembic upgrade head
```

Откатить последнюю миграцию:

```powershell
alembic downgrade -1
```

## Мониторинг и диагностика на VPS

Сервер проекта: `132.243.23.161`.

### Контроль Google OAuth токена

1. Фоновый сервис проверяет Google OAuth и шлет технические уведомления администратору в Telegram.
2. Предупреждение о скором истечении access token управляется переменной:

```env
BACKGROUND_GOOGLE_OAUTH_EXPIRY_WARNING_MINUTES=30
```

3. Если токен перестал обновляться (нужна повторная авторизация), администратор получает техническое уведомление автоматически.

1. Подключение к серверу:

```bash
ssh <user>@132.243.23.161
```

2. Перейти в директорию проекта (где лежит `docker-compose.yml`):

```bash
cd /path/to/project
```

3. Проверить, что сервисы запущены:

```bash
docker compose ps
```

4. Проверить health backend:

```bash
curl http://127.0.0.1:8000/health
```

5. Посмотреть последние логи приложения:

```bash
docker compose logs app --tail 200
```

6. Смотреть логи в реальном времени:

```bash
docker compose logs -f app
```

7. При проблемах с инфраструктурой:

```bash
docker compose logs postgres --tail 100
docker compose logs redis --tail 100
```

## Deploy на VPS (Этап 10)

Сервер: `132.243.23.161`  
Рабочая директория на сервере: `/opt/pomoshchnik-naznacheniya-vstrech`

1. Клонировать/обновить проект:

```bash
cd /opt
git clone https://github.com/OlyaAlekseevna/pomoshchnik-naznacheniya-vstrech.git
cd pomoshchnik-naznacheniya-vstrech
```

2. Скопировать `.env` на сервер и убрать Windows-переносы:

```bash
sed -i '1s/^\xEF\xBB\xBF//' .env
sed -i 's/\r$//' .env
```

3. Запустить сервисы:

```bash
docker compose up -d --build
```

4. Проверить сервисы и health:

```bash
docker compose ps
curl http://127.0.0.1:8000/health
```

5. Проверить webhook-статус бота (для polling URL должен быть пустым):

```bash
docker compose exec -T app python - <<'PY'
import os
import httpx
token = os.environ["TELEGRAM_BOT_TOKEN"]
data = httpx.get(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=15).json()
print(data["result"]["url"], data["result"]["pending_update_count"])
PY
```

6. Автозапуск после перезапуска Docker:

```bash
systemctl restart docker
docker ps
```

Примечание: контейнер `app` автоматически выполняет `alembic upgrade head` при старте.

## Ветки и автодеплой

1. Рабочая ветка разработки: `dev`.
2. Продакшен-ветка: `main`.
3. Автодеплой на VPS запускается только при `push` в `main`.

Подробные инструкции:

1. CI/CD и настройка секретов: `docs/ci-cd-autodeploy.md`.
2. Правильный процесс работы с ветками: `docs/git-branching-dev-main.md`.
