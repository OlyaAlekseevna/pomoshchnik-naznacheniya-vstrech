# CI/CD автодеплой бота на VPS

Документ описывает, как настроен автодеплой и что нужно сделать вручную один раз.

## 1. Алгоритм автодеплоя

1. Вы отправляете код в ветку `main` (`git push origin main`).
2. GitHub запускает workflow `.github/workflows/deploy-vps.yml`.
3. Workflow подключается к VPS по SSH (через секреты GitHub).
4. На сервере запускается `scripts/deploy_vps.sh`.
5. Скрипт выполняет:
   - `git fetch` + `git pull --ff-only`;
   - `docker compose up -d --build --remove-orphans`;
   - проверку `docker compose ps`;
   - проверку `http://127.0.0.1:8000/health`.
6. Если `/health` не возвращает `status=ok`, workflow завершается ошибкой.

## 2. Что уже сгенерировано в репозитории

1. Workflow: `.github/workflows/deploy-vps.yml`.
2. Серверный скрипт деплоя: `scripts/deploy_vps.sh`.
3. Ручной запуск через `workflow_dispatch` отключен: деплой возможен только при `push` в `main`.

## 3. Что нужно настроить вручную

### Шаг 1. Подготовить SSH-ключ для GitHub Actions

На вашей локальной машине:

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ./github_actions_deploy_key
```

Получатся два файла:

1. `github_actions_deploy_key` (приватный ключ) — пойдет в GitHub Secret.
2. `github_actions_deploy_key.pub` (публичный ключ) — пойдет на VPS.

### Шаг 2. Добавить публичный ключ на VPS

Подключитесь к серверу и добавьте ключ в `authorized_keys` пользователя деплоя:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
cat >> ~/.ssh/authorized_keys
# вставьте содержимое github_actions_deploy_key.pub и нажмите Ctrl+D
chmod 600 ~/.ssh/authorized_keys
```

Важно: пользователь деплоя должен иметь доступ к проекту в `/opt/pomoshchnik-naznacheniya-vstrech` и право запускать `docker compose`.

### Шаг 3. Получить known_hosts для сервера

На локальной машине выполните:

```bash
ssh-keyscan -H 132.243.23.161
```

Скопируйте весь вывод (одну или несколько строк) — это будет значение секрета `VPS_KNOWN_HOSTS`.

### Шаг 4. Добавить GitHub Secrets

В репозитории GitHub: `Settings -> Secrets and variables -> Actions -> New repository secret`.

Создайте секреты:

1. `VPS_HOST` = `132.243.23.161`
2. `VPS_PORT` = `22`
3. `VPS_USER` = `<ваш ssh-пользователь на сервере>`
4. `VPS_PROJECT_PATH` = `/opt/pomoshchnik-naznacheniya-vstrech`
5. `VPS_SSH_PRIVATE_KEY` = содержимое файла `github_actions_deploy_key`
6. `VPS_KNOWN_HOSTS` = вывод `ssh-keyscan -H 132.243.23.161`

### Шаг 5. Проверить первый запуск

1. Сделайте тестовый `push` в `main`.
2. Откройте вкладку `Actions` в GitHub и дождитесь `success`.
3. После `success` проверьте на сервере:

```bash
cd /opt/pomoshchnik-naznacheniya-vstrech
docker compose ps
curl http://127.0.0.1:8000/health
```

Ожидаемо: контейнеры `Up`, а в `/health` есть `"status":"ok"`.

## 4. Быстрый чек-лист при проблемах

1. Проверить, что `VPS_SSH_PRIVATE_KEY` вставлен полностью, без обрезки строк.
2. Проверить, что публичный ключ действительно в `~/.ssh/authorized_keys` нужного пользователя.
3. Проверить корректность `VPS_PROJECT_PATH`.
4. Проверить, что на VPS установлен и работает Docker (`docker ps`).
5. Проверить логи приложения:

```bash
cd /opt/pomoshchnik-naznacheniya-vstrech
docker compose logs app --tail 200
```
