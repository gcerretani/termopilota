# --- Fase 1: librerie front-end (Bootstrap, Bootstrap Icons, Chart.js) ---
# Versioni bloccate da package-lock.json, che Dependabot tiene aggiornato.
FROM node:25-slim AS vendor

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY scripts/vendor.js scripts/vendor.js
RUN npm run vendor

# --- Fase 2: applicazione ---
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=vendor /build/static/vendor /app/static/vendor

RUN mkdir -p /app/data

ENV FLASK_APP=app.py

EXPOSE 5001

# Un solo processo: i servizi in background (automazione, campionatore storico)
# partono all'import e non devono essere duplicati ne' forkati con --preload.
CMD ["gunicorn", "-b", "0.0.0.0:5001", "-w", "1", "--threads", "4", "--timeout", "60", "app:app"]
