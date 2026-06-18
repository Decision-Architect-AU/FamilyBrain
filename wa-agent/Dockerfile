FROM python:3.11-slim
RUN apt-get update && apt-get install -y ffmpeg --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
EXPOSE 4002
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "4002"]
