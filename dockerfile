FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    libopus0 \
    libopus-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Informacja dla Render o porcie
EXPOSE 10000

CMD ["python", "bot.py"]
