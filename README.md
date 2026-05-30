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
