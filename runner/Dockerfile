FROM python:3.12-slim
RUN apt-get update && apt-get install -y curl gnupg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work

# Install the repo runtime deps so the runner can execute src/experiment.py etc.
COPY requirements.txt /work/requirements.txt
RUN pip install --no-cache-dir -r /work/requirements.txt

# Runner-side deps (controller client + YAML config parsing)
COPY runner/requirements.txt /work/runner-requirements.txt
RUN pip install --no-cache-dir -r /work/runner-requirements.txt

COPY runner /work/runner

# Install Playwright browsers for experiment execution.
RUN playwright install --with-deps chromium

ENV CONTROLLER_URL=http://controller:8000
ENV REPO_ROOT=/work/repo
CMD ["python", "/work/runner/worker.py"]
