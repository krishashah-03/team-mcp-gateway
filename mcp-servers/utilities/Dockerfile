# syntax=docker/dockerfile:1
FROM python:3.13-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build /install /usr/local
COPY utilities_mcp ./utilities_mcp
USER 10001
EXPOSE 8003
HEALTHCHECK --interval=15s --timeout=4s --start-period=10s --retries=3   CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8003/healthz', timeout=3)"
CMD ["python", "-m", "utilities_mcp"]
