FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg

WORKDIR /app

# Install pinned Python dependencies and the matching Playwright browser once
# at build time so the published image is a self-contained runtime.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && playwright install --with-deps chromium

COPY . /app

ENTRYPOINT ["python"]
CMD ["src/experiment.py", "--trials", "1", "--concurrency", "1", "--max-browsers", "1"]
