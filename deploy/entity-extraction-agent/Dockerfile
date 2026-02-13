ARG PROJECT_ID
ARG ENV
FROM us-docker.pkg.dev/${PROJECT_ID}/${ENV}/python/3.11:latest
LABEL maintainer="ZoomRx"
RUN sudo apt-get --allow-releaseinfo-change update;

RUN mkdir -p /app/entity-extraction-agent
COPY . /app/entity-extraction-agent
WORKDIR /app/entity-extraction-agent
RUN sudo chown -R ferma:ferma /app

# Create venv
RUN python -m venv /app/venv

# Install pip and poetry
RUN /app/venv/bin/pip install --upgrade pip
RUN /app/venv/bin/pip install poetry

# Configure poetry to use existing venv instead of creating its own
ENV VIRTUAL_ENV=/app/venv
ENV PATH="/app/venv/bin:$PATH"

RUN poetry config virtualenvs.create false
RUN poetry install --no-interaction --no-ansi --no-root
