# Mini App на том же VPS, что и бот (домен + HTTPS)

Сервер: `132.243.23.161`  
Проект на сервере: `/opt/pomoshchnik-naznacheniya-vstrech`

## 1) DNS

1. Выберите домен или поддомен для Mini App (пример: `miniapp.example.com`).
2. Создайте `A`-запись на IP `132.243.23.161`.
3. Дождитесь применения DNS.

## 2) Настройки `.env` на VPS

В файле `.env` на сервере задайте:

```env
MINIAPP_DOMAIN=miniapp.example.com
MINIAPP_ENABLED=true
MINIAPP_DEV_LOGIN_ENABLED=false
```

Примечание:
1. `MINIAPP_ENABLED=true` включается только когда готовы к публичному доступу Mini App.
2. В prod `MINIAPP_DEV_LOGIN_ENABLED` должен оставаться `false`.

## 3) Запуск HTTPS-шлюза на том же сервере

В проекте добавлен отдельный compose-файл `docker-compose.public.yml` (Caddy + Let's Encrypt).

Ручной запуск на VPS:

```bash
cd /opt/pomoshchnik-naznacheniya-vstrech
COMPOSE_FILES=docker-compose.yml:docker-compose.public.yml bash scripts/deploy_vps.sh
```

Что делает Caddy:
1. Автоматически получает TLS-сертификат Let's Encrypt.
2. Отдает HTTPS на `443`.
3. Проксирует запросы в backend `app:8000`.

## 4) Проверка

```bash
curl http://127.0.0.1:8000/health
curl -I https://miniapp.example.com/miniapp
```

Ожидаемо:
1. `/health` -> `"status":"ok"`.
2. `https://<домен>/miniapp` -> `200`.

## 5) Telegram Mini App URL

После успешной проверки укажите в BotFather WebApp URL:

```text
https://miniapp.example.com/miniapp
```

## 6) CI/CD (опционально)

Чтобы автодеплой из GitHub Actions сразу поднимал и public-шлюз, добавьте секрет:

1. `VPS_COMPOSE_FILES = docker-compose.yml:docker-compose.public.yml`

Если секрет не задан, деплой остается в старом режиме (только `docker-compose.yml`).
