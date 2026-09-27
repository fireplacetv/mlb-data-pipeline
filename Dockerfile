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

# Copy project files for dependency installation (layer caching)
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --all-groups

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

ENTRYPOINT ["python"]
CMD ["-m", "mlb.pipelines.statcast"]
