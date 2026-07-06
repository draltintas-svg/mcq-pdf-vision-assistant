FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install --include=dev --no-audit --no-fund
COPY frontend ./
RUN npx vite build
