# Работа с ветками `dev` и `main`

## Цель

Зафиксировать безопасный процесс разработки, чтобы не ломать production-бота при доработках Mini App и других функций.

## Правила

1. Все новые изменения делаются в ветке `dev`.
2. `main` используется только как production-ветка.
3. Прямые рабочие изменения в `main` не вносятся.
4. Перенос из `dev` в `main` выполняется только через merge после проверок.
5. `push` в `main` запускает автодеплой на VPS через workflow `.github/workflows/deploy-vps.yml`.
6. `push` в `dev` не должен автоматически деплоить production-код на сервер.
7. Для Mini App merge используется чек-лист `docs/miniapp-dev-main-merge-checklist.md`.

## Обязательные проверки перед merge

1. Обновить локальный `dev`: `git checkout dev && git pull --ff-only origin dev`.
2. Убедиться, что рабочее дерево чистое, кроме явно согласованных untracked-файлов.
3. Прогнать `ruff check .`.
4. Прогнать `pytest -q`.
5. Проверить smoke текущего Telegram-бота.
6. Если Mini App включена, проверить smoke Mini App: `/miniapp`, `/health`, auth, user/admin сценарии.
7. Проверить production feature flags: `MINIAPP_DEV_LOGIN_ENABLED=false`; `MINIAPP_ENABLED` задается выбранной стратегией запуска.

## Релизный цикл

```bash
git checkout dev
git pull --ff-only origin dev
ruff check .
pytest -q

git checkout main
git pull --ff-only origin main
git merge --no-ff dev
git push origin main
```

После `git push origin main` нужно проверить:

1. Workflow `Deploy Bot to VPS` завершился успешно.
2. На VPS контейнеры `app`, `postgres`, `redis` запущены.
3. `/health` возвращает `status=ok`.
4. В логах нет новых критичных ошибок.
5. Бот отвечает на `/start`.
6. Если Mini App включена, вход и базовые сценарии Mini App работают из Telegram.

## Mini App

1. Mini App развивается в `dev` как параллельный канал к Telegram-боту.
2. Бот и Mini App должны работать параллельно на каждом этапе.
3. До релиза в `main` проверяются отсутствие регрессий bot-flow и корректность feature flags Mini App.
4. В production `MINIAPP_DEV_LOGIN_ENABLED` всегда должен быть `false`.
