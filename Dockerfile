# Stage 1: mimalloc 3.3.2, built from source.
# Debian's apt package is mimalloc 2.0.9 — too old; it fragments like glibc. 3.x's
# purge control returns freed memory to the OS. Only the resulting .so is copied
# into the final image (no cmake/curl in production).
FROM python:3.14-slim AS mimalloc
# Source is vendored: GitHub's archive endpoint 503s Cloud Build's egress IPs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake
COPY vendor/mimalloc-3.3.2.tar.gz /tmp/
RUN echo "ca02384e007f46950598500dfaebde5ff9948c1d231f5a81b058799afa64bbbb  /tmp/mimalloc-3.3.2.tar.gz" | sha256sum -c - \
    && tar xzf /tmp/mimalloc-3.3.2.tar.gz \
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
# opteryx-core comes from Artifact Registry ([tool.uv.sources] in pyproject.toml), which needs a
# token: pass one as a BuildKit secret, so it is never in a layer or the image's history -
#   GAR_TOKEN="$(gcloud auth print-access-token)" docker build --secret id=gar_token,env=GAR_TOKEN .
# Without it the install fails with a 401; it never falls back to PyPI.
RUN --mount=type=secret,id=gar_token,required=true \
    pip install --no-cache-dir uv && \
    UV_INDEX_OPTERYX_WHEELS_USERNAME=oauth2accesstoken \
    UV_INDEX_OPTERYX_WHEELS_PASSWORD="$(cat /run/secrets/gar_token)" \
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
#
# ONE worker, deliberately. opteryx-core sizes its memory budgets per PROCESS from
# the whole container: the chunk cache (CHUNK_CACHE_MEMORY_PERCENT x container -
# reserve, 9.4 GiB of 16 GiB), the slab pool's retained slots (4 GiB) and the IO
# budget. Its cache gives way to query memory only in its OWN process, so two
# workers each fill a near-container-sized cache and neither sees the other. The
# 2026-11-14 ClickBench run OOM-killed a worker with one query in flight (224 MB
# left, the killed worker ~11 GB); Q32-Q34 are ~12 GB GROUP BYs on their own.
# Raise this only once opteryx-core divides its budgets by worker count.
ENV WEB_CONCURRENCY=1
# TEMPORARY - per-worker memory / OOM sampler, see app/worker_diagnostics.py.
ENV WORKER_DIAGNOSTICS=1
# `app.preflight` runs FIRST and short-circuits the `&&`: it probes every
# dependency and exits non-zero if one is unreachable, so Cloud Run fails the
# revision and traffic stays on the one already serving. It cannot be only the
# ASGI lifespan -- the server's supervisor binds the socket in the parent and
# respawns dead children, so the port stays open. See app/preflight.py.
CMD python -m app.preflight && python -m app.worker_diagnostics && exec uvicorn app.main:service --host 0.0.0.0 --port $PORT --workers $WEB_CONCURRENCY
