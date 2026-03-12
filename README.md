# Sale Order API Deployment Guide

This repository contains a Flask API for sale-order generation and reporting.

The project is now prepared for:
- Render deployment (`render.yaml`)
- VPS deployment (`gunicorn` + `systemd` + `nginx` examples)

## Capacity target

Baseline tuning included for roughly `10,000 users/day` (normal business traffic pattern):
- `gunicorn`: 2 workers x 4 threads
- DB pool: size 20 + overflow 40
- Query indexes for common filters/sorts

For larger spikes, scale horizontally (multiple instances) and keep Postgres external.

## 1) Render deployment

1. Push this repo to GitHub.
2. In Render, create service using **Blueprint** and point to this repo.
3. Render reads [`render.yaml`](./render.yaml) and creates `sale-order-backend`.
4. Set required secrets in Render dashboard:
   - `SECRET_KEY`
   - `JWT_SECRET` (can be same as `SECRET_KEY`)
   - `DATABASE_URL` (Postgres)
   - `SUPABASE_URL` (if using Supabase storage)
   - `SUPABASE_KEY` (service role key for backend)
   - `USER1` (example: `admin:$2b$12$...`)
   - `ADMIN_USERS` (comma-separated, example: `admin`)
5. Deploy.

Health endpoints:
- Liveness: `/api/v1/health`
- Readiness: `/api/v1/ready`

## 2) VPS deployment (Ubuntu)

Use the templates:
- [`deploy/vps/sale-order-api.service.example`](./deploy/vps/sale-order-api.service.example)
- [`deploy/vps/nginx.sale-order-api.conf.example`](./deploy/vps/nginx.sale-order-api.conf.example)
- [`.env.production.example`](./.env.production.example)

### Steps

1. Install system packages:
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx
```

2. Copy code to `/opt/sale-order` and create virtualenv:
```bash
cd /opt/sale-order
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

3. Create production env file:
```bash
cp .env.production.example .env.production
# edit .env.production with real values
```

4. Configure systemd service:
```bash
sudo cp deploy/vps/sale-order-api.service.example /etc/systemd/system/sale-order-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now sale-order-api
sudo systemctl status sale-order-api
```

5. Configure nginx reverse proxy:
```bash
sudo cp deploy/vps/nginx.sale-order-api.conf.example /etc/nginx/sites-available/sale-order-api
sudo ln -s /etc/nginx/sites-available/sale-order-api /etc/nginx/sites-enabled/sale-order-api
sudo nginx -t
sudo systemctl reload nginx
```

6. (Recommended) Add SSL:
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d api.example.com
```

## 3) Production checklist

- Use Postgres in production (`DATABASE_URL`)
- Keep `REQUIRE_EXTERNAL_DB=1`
- Use secure secrets and bcrypt hashes only
- Restrict `CORS_ALLOWED_ORIGINS` to trusted frontend domain(s)
- Keep `SUPABASE_KEY` server-side only
- Monitor:
  - app logs
  - `/api/v1/ready`
  - DB connections and slow queries

## 4) Quick smoke test

```bash
curl -s http://127.0.0.1:10000/api/v1/health
curl -s http://127.0.0.1:10000/api/v1/ready
```

## 5) Docker Compose (Production-style with Caddy)

Files added:
- `Dockerfile`
- `docker-compose.yml`
- `Caddyfile`
- `.env.docker.example`

### Run

```bash
cp .env.docker.example .env
# edit .env with real secrets/domain + Supabase DATABASE_URL
docker compose up -d --build
```

### Stack

- `api`: Flask + gunicorn (internal only)
- `db`: Postgres 16 (optional, only when using `localdb` profile)
- `caddy`: reverse proxy + HTTPS termination (ports 80/443)

### Notes

- Set `SITE_ADDRESS` to your real domain (for example `api.example.com`) so Caddy can issue certificates.
- For local testing, you can set `SITE_ADDRESS=localhost`.
- This compose file is **Supabase-first**: `DATABASE_URL` is required and should point to Supabase Postgres.
- If you want local Postgres instead, enable profile:
  `COMPOSE_PROFILES=localdb docker compose up -d --build`
- API public health via Caddy: `https://<SITE_ADDRESS>/api/v1/health`
