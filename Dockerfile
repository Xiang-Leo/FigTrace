FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml requirements.lock README.md LICENSE ./
COPY figtrace/ ./figtrace/
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
COPY --from=frontend /build/dist ./frontend/dist
ENV FIGTRACE_DATA_DIR=/data
ENV FIGTRACE_ALLOWED_ROOTS=/sources
ENV FIGTRACE_FRONTEND=/app/frontend/dist
EXPOSE 8765
VOLUME ["/data"]
CMD ["figtrace", "--host", "0.0.0.0", "--port", "8765"]
