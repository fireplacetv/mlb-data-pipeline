FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

# Set up Python environment outside the repo (so bind-mount doesn't hide it)
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
# The mlb package is imported from the bind-mounted source, not installed into the venv
ENV PYTHONPATH=/app/src

# Copy project files for dependency installation (layer caching)
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --all-groups --no-install-project

# Install dbt packages (requires dbt_project.yml and packages.yml)
COPY dbt/dbt_project.yml dbt/packages.yml dbt/
RUN dbt deps --project-dir dbt --profiles-dir dbt

# Build args for non-root user (Linux)
ARG UID=1000
ARG GID=1000

# Create non-root user with UID/GID
RUN groupadd -g ${GID} mlb && useradd -u ${UID} -g ${GID} -m mlb

# Copy the rest of the source code
COPY --chown=mlb:mlb . .

# Switch to non-root user
USER mlb

# No ENTRYPOINT: `docker compose run --rm pipeline <command>` runs <command> as given
CMD ["bash"]
