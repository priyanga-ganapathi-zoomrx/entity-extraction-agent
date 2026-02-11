ARG PROJECT_ID
ARG ENV
FROM us-docker.pkg.dev/${PROJECT_ID}/${ENV}/python/3.12:latest
LABEL maintainer="ZoomRx"
RUN sudo apt-get --allow-releaseinfo-change update;

# Install Poetry
RUN pip install poetry

RUN mkdir -p /app/entity-extraction-agent
COPY . /app/entity-extraction-agent
WORKDIR /app/entity-extraction-agent
RUN sudo chown -R ferma:ferma /app

# Create venv inside the project directory (.venv/)
RUN poetry config virtualenvs.in-project true
RUN poetry install --no-interaction --no-ansi
