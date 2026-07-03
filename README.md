# vpn_control_web

Web-панель управления VPN-серверами корпоративного OpenWrt (europeya): статус, таблица серверов
с latency, ручной выбор, режимы auto-sticky/auto-best/manual, включение/выключение VPN.

Архитектура: FastAPI + Jinja2/HTMX на Docker-хосте `vpn-control` (192.168.2.22) ходит по SSH
(forced command, whitelist) на OpenWrt и вызывает `vpnctl` — тонкий control plane, который
управляет xray/sing-box. Routing остаётся у pbr. История latency пишется в SQLite на стороне web.

```
Браузер (management LAN) ──:8090──> vpn_control_web (VM 101)
                                        │ ssh -i id_ed25519 (forced command vpnctl-ssh)
                                        v
OpenWrt: vpnctl status|list|refresh|check|mode|select|enable|disable|logs  (все — JSON, flock)
```

## Каталоги

- `app/` — FastAPI-приложение (poller, кэш, REST + HTMX-дашборд).
- `openwrt/` — исходники control plane для роутера (`vpnctl`, `vpnctl-ssh`, модули
  parser/checker/renderer/worker, boot-скрипт) + `deploy.sh`.
- `tests/` — pytest (парсинг ответов vpnctl, API с mock SSH).

## Запуск web (VM 101)

1. `git clone` в `/opt/vpn_control_web`, `cp .env.example .env`.
2. `ssh/id_ed25519` (0600) — ключ, чей pub добавлен в authorized_keys OpenWrt с forced command;
   `ssh/known_hosts` — `ssh-keyscan 192.168.253.112`.
3. `mkdir -p data && chown 10001:10001 data` (SQLite).
4. `docker compose up -d --build` → `http://192.168.2.22:8090/`.

Авторизации нет: порт публикуется только на management-LAN адресе — это и есть контроль доступа.

## Деплой control plane на OpenWrt

`./openwrt/deploy.sh [phase]` — копирует скрипты через Proxmox-прыжок
(`root@192.168.2.20` → `root@192.168.253.112`). Фаза (файл `/etc/vpn-control/phase`)
управляет whitelist'ом `vpnctl-ssh`:

| Фаза | Доступно web-у |
|---|---|
| 1 | status, list, logs (read-only) |
| 2 | + refresh, check |
| 3 | + mode, select, enable, disable |

Cutover с legacy `vpn-subscription-manager` (фаза 3): закомментировать его cron-строку,
`vpnctl migrate`, добавить `*/5 * * * * /usr/libexec/vpnctl cron`, старый скрипт → `.bak`.

Rollback: вернуть cron-строку на старый скрипт; крайний случай — Proxmox snapshot
`pre_vpnctl_20260703` VM 100.

## Тесты

```bash
pip install -e '.[dev]'
pytest
```

Секреты (subscription URL) живут только на роутере в `/etc/vpn-control/subscription.url` (0600) —
в репозиторий и документацию не попадают.
