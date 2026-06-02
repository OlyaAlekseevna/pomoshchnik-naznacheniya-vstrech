# Merge checklist: `dev` -> `main` после Mini App MVP

Дата: 31.05.2026  
Статус: `ready_for_use`

## 1. Цель

Безопасно перенести изменения Mini App из `dev` в `main` без регрессий текущего Telegram-бота.

## 2. Что уже готово

1. Mini App MVP принят в `dev` (этапы `6/6` закрыты).
2. Ключевые автопроверки пройдены (`ruff`, `pytest`).
3. User/admin parity, hardening и приемочный чеклист Mini App закрыты.

## 3. Обязательный pre-merge checklist

- [x] 1. Убедиться, что merge выполняется только `dev -> main` (без прямых коммитов в `main`).
- [x] 2. Обновить локальный `dev`: `git checkout dev && git pull --ff-only origin dev`.
- [x] 3. Убедиться, что рабочее дерево чистое (кроме явно согласованных untracked файлов).
- [x] 4. Повторно прогнать проверки: `ruff check .` и `pytest -q`.
- [x] 5. Прогнать smoke текущего Telegram-бота (user-flow + admin-flow).
- [x] 6. Прогнать smoke Mini App (`GET /miniapp`, auth, `Записаться`, `Мои заявки`, admin-блок).
- [x] 7. Проверить feature flags для prod: `MINIAPP_DEV_LOGIN_ENABLED=false` (обязательно) и `MINIAPP_ENABLED` по выбранной стратегии релиза (см. раздел 4).
- [x] 8. Проверить, что миграции актуальны и в `alembic` нет незакоммиченных изменений.
- [x] 9. Открыть PR `dev -> main` с кратким отчетом по проверкам.
- [x] 10. После merge проверить GitHub Actions deploy и `/health` на VPS.

## 4. Стратегия включения Mini App в `main`

### Вариант A (рекомендуется для осторожного релиза)

1. В `main` оставить `MINIAPP_ENABLED=false`.
2. Деплой проходит с нулевым влиянием на прод-пользователей.
3. Mini App включается позже отдельным управляемым шагом через конфиг.

### Вариант B (сразу включить Mini App в prod)

1. В `main` включить `MINIAPP_ENABLED=true`.
2. Обязательно оставить `MINIAPP_DEV_LOGIN_ENABLED=false`.
3. Перед публикацией проверить Telegram Mini App URL (https) и открытие внутри Telegram.

### Выбранная стратегия (обновлено 02.06.2026)

- В коде сохранен безопасный default: `MINIAPP_ENABLED=false`.
- На production VPS Mini App включена через env: `MINIAPP_ENABLED=true`.
- Обязательное правило для prod: `MINIAPP_DEV_LOGIN_ENABLED=false`.
- Финальный релиз выполнен merge `dev -> main`: commit `27c3ae1`.

## 5. Post-merge контроль (обязательный)

- [x] 1. Workflow `Deploy Bot to VPS` запустился после push в `main`; результат `failure` на шаге `Validate required secrets`, причина — отсутствуют обязательные GitHub Actions secrets для SSH-деплоя.
- [x] 2. Выполнен ручной fallback deploy на VPS из `main` (`DEPLOY_SHA=27c3ae1`, `COMPOSE_FILES=docker-compose.yml:docker-compose.public.yml`).
- [x] 3. На VPS: `docker compose ps` показывает `Up` для `app/caddy/postgres/redis`.
- [x] 4. `https://calendar.monvera.su/health` возвращает `status=ok`.
- [x] 5. В логах `app` нет новых критических ошибок (`TelegramConflictError`, ошибок БД, ошибок запуска).
- [x] 6. Bot API menu button проверен: `Открыть Mini App` ведет на `https://calendar.monvera.su/miniapp?v=20260602-telegram-auth`.
- [x] 7. Mini App включена и отдает свежую страницу `Вход через Telegram` с no-cache headers.

## 6. Критерий готовности к merge

Merge `dev -> main` выполнен. Приложение готово к ручной приемке владельцем; для будущего автоматического деплоя нужно добавить недостающие GitHub Actions secrets.
