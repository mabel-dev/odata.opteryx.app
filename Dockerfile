FROM python:3.14-slim

# Create a non-root user 'norris'
RUN useradd --create-home norris
WORKDIR /home/norris

# Install build deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

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
EXPOSE 8080

CMD uvicorn app.main:service --host 0.0.0.0 --port $PORT
