FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000

CMD exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
