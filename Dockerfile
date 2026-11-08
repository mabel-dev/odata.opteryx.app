# Stage 1: mimalloc 3.3.2, built from source.
# Debian's apt package is mimalloc 2.0.9 — too old; it fragments like glibc. 3.x's
# purge control returns freed memory to the OS. Only the resulting .so is copied
# into the final image (no cmake/curl in production).
FROM python:3.14-slim AS mimalloc
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake curl ca-certificates \
    && curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors https://github.com/microsoft/mimalloc/archive/refs/tags/v3.3.2.tar.gz | tar xz \
    && cmake -S mimalloc-3.3.2 -B /mi -DCMAKE_BUILD_TYPE=Release \
        -DMI_BUILD_SHARED=ON -DMI_BUILD_STATIC=OFF -DMI_BUILD_OBJECT=OFF -DMI_BUILD_TESTS=OFF \
    && cmake --build /mi -j

# Stage 2: Final
FROM python:3.14-slim

# Create a non-root user 'norris'
RUN useradd --create-home norris
WORKDIR /home/norris

# Install build deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

# Bring in the mimalloc 3.3.2 shared lib built in the mimalloc stage (preloaded below).
COPY --from=mimalloc /mi/libmimalloc.so.3.3 /usr/local/lib/libmimalloc.so.3

# Copy project pyproject from the build context (repository root) and install with uv.
# NOTE: Run this from the repository root so `pyproject.toml` is available in the
# build context. Example:
#   docker build -f Dockerfile -t opteryx-odata .
COPY pyproject.toml ./pyproject.toml
# Install uv, then install project dependencies into .venv. `--no-install-project`
# installs only the dependencies; the service code is imported directly by uvicorn
# from ./app (copied below), not installed as a package.
RUN pip install --no-cache-dir uv && \
    uv sync --no-install-project || \
    (echo "uv sync failed; ensure pyproject.toml is present and valid in the build context" && exit 1)

# Copy service code
COPY app ./app
COPY .well-known ./.well-known
RUN chown -R norris:norris /home/norris
USER norris

ENV PATH=/home/norris/.venv/bin:$PATH
ENV PORT=8080
# Route ALL process allocations through mimalloc 3.3 — see worker.opteryx
# Dockerfile for the full rationale. The native query engine's multi-threaded
# workers fragment glibc's per-thread arenas and never return the memory, so RSS
# climbs to OOM across queries; mimalloc returns freed pages to the OS. LD_PRELOAD
# is the only reliable global swap (ld.so reads it at exec, before Python — cannot
# be set in-process). PURGE_DELAY=100ms = the memory/speed knee (flat RSS, ~+4%).
ENV LD_PRELOAD="/usr/local/lib/libmimalloc.so.3"
ENV MIMALLOC_PURGE_DELAY=100
EXPOSE 8080

# One event loop for the whole process otherwise -- everything the ASGI layer
# does (request handling, the auth dependency, response compression) funnels
# through a single core no matter how many vCPUs the container gets. WEB_CONCURRENCY
# is a knob, not a fixed value: size it to the container's vCPU allocation. Keep it
# modest relative to vCPUs, though -- opteryx-core spawns its own native worker
# threads per query, and those multiply per uvicorn process too.
ENV WEB_CONCURRENCY=2
# TEMPORARY - per-worker memory / OOM sampler, see app/worker_diagnostics.py.
ENV WORKER_DIAGNOSTICS=1
# `app.preflight` runs FIRST and short-circuits the `&&`: it probes every
# dependency and exits non-zero if one is unreachable, so Cloud Run fails the
# revision and traffic stays on the one already serving. It cannot be only the
# ASGI lifespan -- the server's supervisor binds the socket in the parent and
# respawns dead children, so the port stays open. See app/preflight.py.
CMD python -m app.preflight && python -m app.worker_diagnostics && exec uvicorn app.main:service --host 0.0.0.0 --port $PORT --workers $WEB_CONCURRENCY
