FROM python:3.12-slim

# System dependencies:
#   ffmpeg    -> audio streaming/transcoding (discord.FFmpegPCMAudio)
#   libopus0  -> Discord voice encoding (required by discord.py voice)
#   nodejs    -> yt-dlp YouTube JS challenge solving (js_runtimes / remote_components)
#   ca-certificates -> TLS for yt-dlp / discord
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libopus0 \
        nodejs \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so they cache across code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Run the bot. properties.properties and cookies.txt are provided at runtime
# (see docker-compose.yml / the docker run -v flags) rather than baked in.
CMD ["python", "-u", "shehrazad.py"]
