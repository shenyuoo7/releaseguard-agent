FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN useradd --create-home --uid 10001 releaseguard

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=releaseguard:releaseguard src ./src
COPY --chown=releaseguard:releaseguard knowledge_base ./knowledge_base
COPY --chown=releaseguard:releaseguard sample_projects ./sample_projects
COPY --chown=releaseguard:releaseguard evals ./evals

USER releaseguard

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

CMD ["python", "-m", "uvicorn", "releaseguard_agent.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
