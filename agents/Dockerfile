FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir setuptools && pip install --no-cache-dir -r requirements.txt
COPY src ./src
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
CMD ["python", "src/main.py"]
