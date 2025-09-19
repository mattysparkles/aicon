FROM python:3.13-slim

WORKDIR /app
# System deps for audio processing (pydub needs ffmpeg)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app

ENV PORT=5050 HOST=0.0.0.0
EXPOSE 5050
CMD ["python", "app.py"]
