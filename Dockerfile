# The official python images publish no free-threaded tag, so this base image
# provides only the system libraries and a bootstrap interpreter for uv. uv
# then provisions the free-threaded CPython 3.14 build ("3.14t", pinned in
# .python-version) and builds the .venv against it.
FROM python:3.14-slim

# Create a non-root user 'norris'
RUN useradd --create-home norris
WORKDIR /home/norris

# Install build deps (needed to compile any dependency that lacks a
# free-threaded/cp314t wheel, e.g. grpcio, google-crc32c).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy project metadata from the build context (repository root) and install
# with uv. Run the build from the repository root so both `pyproject.toml` and
# `.python-version` are available in the build context. Example:
#   docker build -f Dockerfile -t opteryx-odata .
COPY pyproject.toml ./pyproject.toml
COPY .python-version ./.python-version
# Install uv, provision the free-threaded interpreter named in .python-version,
# and install project dependencies into .venv. `--no-install-project` installs
# only the dependencies (the service code is imported directly by uvicorn from
# ./app, not installed as a package).
RUN pip install --no-cache-dir uv && \
    uv python install 3.14t && \
    uv sync --no-install-project || \
    (echo "uv sync failed; ensure pyproject.toml and .python-version are present in the build context" && exit 1)

# Copy service code
COPY app ./app
RUN chown -R norris:norris /home/norris
USER norris

ENV PATH=/home/norris/.venv/bin:$PATH
ENV PORT=8080
EXPOSE 8080

CMD uvicorn app.main:service --host 0.0.0.0 --port $PORT
