# AURA Tickets API

API сервер для системы билетов клуба AURA.

## Endpoints

| Метод | Endpoint | Описание |
|-------|----------|----------|
| POST | `/api/tickets/` | Создать билет |
| GET | `/api/tickets/` | Список билетов |
| GET | `/api/tickets/{order_id}` | Получить билет по order_id |
| GET | `/api/tickets/token/{token}` | Получить билет по токену |
| PATCH | `/api/tickets/{order_id}/cancel` | Отменить билет |
| POST | `/api/verify` | Проверить QR-код |
| GET | `/api/stats/` | Статистика |
| GET | `/api/history/` | История для сканера |
| GET | `/health` | Health check |
| GET | `/docs` | Swagger документация |

## Локальный запуск

```bash
# Установка зависимостей
pip install -r requirements.txt

# Запуск
uvicorn app.main:app --reload
```

## Deploy на Railway

1. Push на GitHub
2. В Railway: + New → GitHub Repo
3. Добавить переменные:
   - `DATABASE_URL`
   - `QR_SECRET_KEY`
   - `API_SECRET_KEY`

## Переменные окружения

```env
DATABASE_URL=postgresql://user:pass@host:port/db
QR_SECRET_KEY=aura_club_secret_2024
API_SECRET_KEY=aura_api_secret_key_2024_random
APP_NAME=AURA Tickets API
DEBUG=false
```

## Формат QR-кода

```
AURA|version|order_id|ticket_type|date|name|email|phone|price|paid|token|signature
```

## Пример использования

### Создание билета
```bash
curl -X POST "https://your-api.railway.app/api/tickets/" \
  -H "Content-Type: application/json" \
  -d '{
    "order_id": "123456",
    "customer_name": "John Doe",
    "customer_email": "john@example.com",
    "ticket_type": "VIP",
    "event_date": "25.12",
    "price": 150
  }'
```

### Проверка QR
```bash
curl -X POST "https://your-api.railway.app/api/verify" \
  -H "Content-Type: application/json" \
  -d '{
    "qr_data": "AURA|1|123456|VIP|25.12|JohnDoe|john@example.com|+48123456789|150|1|token|signature",
    "scanner_id": "scanner_1"
  }'
```

### Получение статистики
```bash
curl "https://your-api.railway.app/api/stats/"
```

## Backup и Restore Postgres

Критичная аналитика хранится в PostgreSQL, в том числе:
- `tickets.created_at`
- `tickets.first_scan_at`
- `tickets.scan_count`
- `scan_history`

Чтобы не потерять эти данные, в проект добавлены локальные снапшоты БД без `pg_dump`.

### Сделать backup вручную

```bash
python backup_postgres.py
```

Скрипт:
- читает `DATABASE_URL` из `.env`
- сохраняет все таблицы `public` в `backups/postgres/*.json.gz`
- автоматически держит только последние 30 копий

### Восстановить из backup

```bash
python restore_postgres_backup.py --latest
```

Перед восстановлением скрипт сам делает дополнительную safety-копию текущей базы.

Для проверки без изменений:

```bash
python restore_postgres_backup.py --latest --dry-run
```

### Автоматический backup в Windows

Рекомендуемый минимум: 1 раз в день. Если сканы критичны, лучше каждые 1-2 дня, а не реже.

Создать задачу Windows Scheduler:

```powershell
.\install_backup_task.ps1 -EveryDays 1 -At 06:00
```

Что это даёт:
- каждый запуск пишет новый compressed snapshot
- логи запуска лежат в `backups/logs/`
- restore можно сделать в любой момент локально из последней копии

Важно:
- restore перезаписывает текущие таблицы `public`
- делать restore лучше, когда сканирование остановлено, иначе новые сканы в этот момент могут потеряться
