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

# git is a runtime dependency now: the worker shells out to it to clone
# repositories. Installed in runtime (not just dev) because the worker runs the
# production image. --no-install-recommends keeps the layer minimal. git is not
# version-pinned: the exact distro version is dropped from Debian's repo on each
# point release, which would break the build; we take the base image's patched
# git instead.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=devpilot:devpilot alembic.ini ./
COPY --chown=devpilot:devpilot alembic ./alembic

USER devpilot
EXPOSE 8000

CMD ["uvicorn", "devpilot.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]


# ---------------------------------------------------------------------------
# Development / test image.
#
# Deliberately a separate stage rather than adding pytest to `runtime`. A
# production image that ships a test framework is a defect: it inflates the
# image, widens the dependency surface that has to be scanned and patched, and
# lets test-only code be imported by accident in production. `runtime` is what
# deploys; `dev` is what you run tests in. They share every layer up to this
# point, so the split costs one cached layer and no rebuild time.
#
# `docker compose` builds this target for local development. CI builds
# `runtime` as well, which is what keeps the lean image honest -- if the
# production image ever grows a test dependency, that job is where it shows.
# ---------------------------------------------------------------------------
FROM runtime AS dev

USER root

# The test configuration lives in pyproject.toml, so pytest needs it present
# to find rootdir, testpaths, asyncio_mode and the registered markers. Without
# it pytest runs with defaults and every async test errors out.
COPY --chown=devpilot:devpilot pyproject.toml ./
COPY --chown=devpilot:devpilot src ./src
COPY --chown=devpilot:devpilot tests ./tests

# Editable install so the source bind-mounted over /app/src at run time is the
# source under test, rather than a stale copy baked into the image.
# --no-cache-dir explicitly: PIP_NO_CACHE_DIR is set in the builder stage and
# ENV does not survive a FROM, so without this the wheel cache is baked into
# the image layer.
RUN pip install --no-cache-dir -e ".[dev]"

USER devpilot
