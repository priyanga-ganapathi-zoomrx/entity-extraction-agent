# Use congress-core-utils as base image
ARG PROJECT_ID
ARG ENV

FROM us-docker.pkg.dev/${PROJECT_ID}/${ENV}/core-utils:latest
LABEL maintainer="ZoomRx"

RUN sudo apt-get --allow-releaseinfo-change update;

# maintainer info
LABEL maintainer="FERMA"

# Install uv (fast Python package installer)
RUN sudo apt-get --allow-releaseinfo-change update;
RUN curl -LsSf https://astral.sh/uv/0.9.22/install.sh | sh

# Add uv to PATH
ENV PATH="/home/ferma/.local/bin:${PATH}"

# ======================================
# Copy stage - application
# ======================================
RUN mkdir -p /app/entity-extraction-agent
COPY . /app/entity-extraction-agent
WORKDIR /app/entity-extraction-agent

# Give ownership to ferma user
RUN sudo chown -R ferma:ferma /app

# Create virtual environment and install dependencies from pyproject.toml
RUN uv venv
RUN uv sync
