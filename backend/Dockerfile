# Multi-stage build. The builder carries compilers and build metadata; the
# runtime image carries neither. Later milestones add sentence-transformers,
# whose build dependencies are large enough that keeping them out of the
# runtime image is the difference between a lean image and a bloated one.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dependency layer is copied and installed before source so that editing a
# source file does not invalidate the dependency cache on every rebuild.
COPY pyproject.toml ./
RUN mkdir -p src/devpilot && touch src/devpilot/__init__.py && pip install .

COPY src ./src
RUN pip install --no-deps .


FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system devpilot && useradd --system --gid devpilot --create-home devpilot

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=devpilot:devpilot alembic.ini ./
COPY --chown=devpilot:devpilot alembic ./alembic

USER devpilot
EXPOSE 8000

CMD ["uvicorn", "devpilot.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
