FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

# Add the current directory to PYTHONPATH so modules are found
ENV PYTHONPATH="/app"

COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install --no-cache-dir --timeout=300 --retries=5 -r requirements.txt

COPY . .

CMD ["python", "main.py"]