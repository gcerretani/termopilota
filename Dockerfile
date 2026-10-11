# --- Fase 1: librerie front-end (Bootstrap, Bootstrap Icons, Chart.js) ---
# Versioni bloccate da package-lock.json, che Dependabot tiene aggiornato.
FROM node:24-slim AS vendor

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY scripts/vendor.js scripts/vendor.js
RUN npm run vendor

# --- Fase 2: applicazione ---
FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY --from=vendor /build/src/termopilota/static/vendor ./src/termopilota/static/vendor
RUN pip install --no-cache-dir .

# I dati stanno in /app/data (volume): percorsi.py li cerca li' dalla WORKDIR.
RUN mkdir -p /app/data

EXPOSE 5001

# Un solo processo: i servizi in background (automazione, campionatore storico)
# partono all'import e non devono essere duplicati ne' forkati con --preload.
CMD ["gunicorn", "-b", "0.0.0.0:5001", "-w", "1", "--threads", "4", "--timeout", "60", "termopilota.app:app"]
