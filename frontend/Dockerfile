# Frontend image: build the SPA + demo with pnpm, serve it (and reverse-proxy
# /v1 -> backend, /axor -> proxy) with nginx. No private deps — builds anywhere.
FROM node:22-slim AS build
WORKDIR /app/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend ./
RUN pnpm build

FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
EXPOSE 8080
