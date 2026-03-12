FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=10000

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

RUN addgroup --system app \
    && adduser --system --ingroup app --home /app app \
    && mkdir -p /var/data/uploads /var/data/reports \
    && chown -R app:app /app /var/data

USER app

EXPOSE 10000

CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:application"]
