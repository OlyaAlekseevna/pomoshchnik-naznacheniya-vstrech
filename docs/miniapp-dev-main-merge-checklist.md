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
- [ ] 9. Открыть PR `dev -> main` с кратким отчетом по проверкам.
- [ ] 10. После merge проверить GitHub Actions deploy и `/health` на VPS.

## 4. Стратегия включения Mini App в `main`

### Вариант A (рекомендуется для осторожного релиза)

1. В `main` оставить `MINIAPP_ENABLED=false`.
2. Деплой проходит с нулевым влиянием на прод-пользователей.
3. Mini App включается позже отдельным управляемым шагом через конфиг.

### Вариант B (сразу включить Mini App в prod)

1. В `main` включить `MINIAPP_ENABLED=true`.
2. Обязательно оставить `MINIAPP_DEV_LOGIN_ENABLED=false`.
3. Перед публикацией проверить Telegram Mini App URL (https) и открытие внутри Telegram.

### Выбранная стратегия (01.06.2026)

- Выбрано: **Вариант A** (`MINIAPP_ENABLED=false` в `main`, мягкий запуск).
- Обязательное правило для prod: `MINIAPP_DEV_LOGIN_ENABLED=false`.

## 5. Post-merge контроль (обязательный)

- [ ] 1. Workflow `Deploy Bot to VPS` завершился `success`.
- [ ] 2. На VPS: `docker compose ps` показывает `Up` для `app/postgres/redis`.
- [ ] 3. `curl http://127.0.0.1:8000/health` возвращает `status=ok`.
- [ ] 4. В логах `app` нет новых критических ошибок (`TelegramConflictError`, ошибок БД, ошибок запуска).
- [ ] 5. Бот отвечает на `/start` и базовые команды.
- [ ] 6. При включенном Mini App проверен вход и создание тестовой заявки.

## 6. Критерий готовности к merge

Merge `dev -> main` выполняется только когда все пункты разделов 3 и 5 отмечены как выполненные.
