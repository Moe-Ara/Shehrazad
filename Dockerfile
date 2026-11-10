FROM python:3.12-slim

# System dependencies:
#   ffmpeg          -> audio decoding/normalisation (discord.FFmpegPCMAudio)
#   libopus0        -> Discord voice encoding
#   nodejs          -> lets yt-dlp solve YouTube JS challenges when a client needs it
#   ca-certificates -> TLS for yt-dlp / Discord
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 nodejs ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_PATH=/app/data/shehrazad.db \
    HEALTH_PORT=8080

WORKDIR /app

# Python deps first so they cache across code changes
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY musicbot ./musicbot
COPY shehrazad.py ./

# Run as an unprivileged user; /app/data holds the SQLite database (mount a volume).
RUN useradd --create-home --uid 10001 shehrazad \
    && mkdir -p /app/data \
    && chown -R shehrazad:shehrazad /app/data
USER shehrazad

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"

# Secrets/config come from the environment (.env via docker compose); never baked in.
CMD ["python", "-m", "musicbot"]
