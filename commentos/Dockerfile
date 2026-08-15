FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Vendor the React runtime so the page has zero CDN dependencies at runtime.
RUN mkdir -p static/assets && \
    curl -fsSL https://unpkg.com/react@18.3.1/umd/react.production.min.js \
        -o static/assets/react.production.min.js && \
    curl -fsSL https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js \
        -o static/assets/react-dom.production.min.js && \
    curl -fsSL https://unpkg.com/htm@3.1.1/dist/htm.js \
        -o static/assets/htm.js

COPY src/ ./src/
COPY static/index.html ./static/index.html

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "4004"]
