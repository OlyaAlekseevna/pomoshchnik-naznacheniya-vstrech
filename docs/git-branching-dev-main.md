# Работа с ветками `dev` и `main`

Этот проект использует простую схему:

1. `dev` — разработка и тестирование.
2. `main` — продакшен.

Деплой на сервер запускается только при `push` в `main`.

## 1. Правила

1. Все новые изменения делаем в `dev`.
2. В `main` напрямую не работаем.
3. После проверки в `dev` переносим изменения в `main` через merge.
4. Только `push` в `main` запускает автодеплой на VPS.
5. `push` в `dev` никогда не деплоит код на сервер.

## 2. Ежедневный рабочий цикл

1. Переключиться на `dev`:

```bash
git checkout dev
git pull origin dev
```

2. Сделать изменения, commit, push:

```bash
git add .
git commit -m "feat: описание изменения"
git push origin dev
```

3. Протестировать на `dev` (локально и/или в тестовом окружении).
4. Когда все готово к релизу, влить `dev` в `main`:

```bash
git checkout main
git pull origin main
git merge --no-ff dev
git push origin main
```

5. После `git push origin main` проверить GitHub Actions:
   - Workflow `Deploy Bot to VPS` должен завершиться со статусом `success`.

## 3. Быстрая проверка, что деплой не идет из `dev`

1. Сделайте `push` в `dev`.
2. Откройте вкладку `Actions`.
3. Убедитесь, что workflow `Deploy Bot to VPS` не запускался.

## 4. Рекомендация по защите веток в GitHub

В репозитории включите Branch protection rules:

1. Для `main`:
   - запрет прямого push (Require a pull request before merging);
   - требовать успешные проверки перед merge.
2. Для `dev`:
   - по желанию оставить прямой push или тоже ограничить через PR.

Это уменьшает риск случайного деплоя в прод.
