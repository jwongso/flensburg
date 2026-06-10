FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

COPY . .

ARG APP_MODULE=apps.nz_tenancy_app:app
ENV APP_MODULE=${APP_MODULE}

EXPOSE 8000

CMD uvicorn ${APP_MODULE} --host 0.0.0.0 --port 8000
