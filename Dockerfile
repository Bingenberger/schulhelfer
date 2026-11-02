FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY app ./app
COPY templates ./templates
COPY static ./static
COPY migrations ./migrations
COPY alembic.ini seed.py ./
RUN groupadd --gid 10001 schulhelfer && useradd --uid 10001 --gid 10001 --no-create-home schulhelfer \
    && mkdir -p /app/data && chown 10001:10001 /app/data && chmod 700 /app/data
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "app.run"]

FROM runtime AS tests
COPY requirements-test.lock ./
USER 0
RUN pip install --no-cache-dir --require-hashes -r requirements-test.lock
USER 10001:10001
COPY tests ./tests
COPY contract_tests ./contract_tests
CMD ["python", "-m", "pytest", "-q"]

FROM runtime AS final
