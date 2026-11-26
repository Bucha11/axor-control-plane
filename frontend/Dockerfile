# Frontend image: build the SPA + demo with pnpm, serve it (and reverse-proxy
# /v1 -> backend, /axor -> proxy) with nginx. No private deps — builds anywhere.
FROM node:22-slim AS build
WORKDIR /app/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend ./
# Checkout link for the Pricing CTA (launch-readiness §5): bake a payment link
# in at build time — no code change, just a build arg.
ARG VITE_CHECKOUT_URL=""
ENV VITE_CHECKOUT_URL=${VITE_CHECKOUT_URL}
# Card checkout through Paddle (src/paddle.ts). The client token and price ids
# are public — Paddle.js runs in the browser — and self-hosted installs buy from
# their own Pricing too: the release workflow passes the vendor's live values
# (repository variables) into the published image. Empty = the link above. Hosted or not is the backend's AXOR_HOSTED, not a
# build flag: hosted and self-hosted run this same image.
ARG VITE_PADDLE_CLIENT_TOKEN=""
ARG VITE_PADDLE_ENV=""
ARG VITE_PADDLE_PRICES=""
ENV VITE_PADDLE_CLIENT_TOKEN=${VITE_PADDLE_CLIENT_TOKEN} \
    VITE_PADDLE_ENV=${VITE_PADDLE_ENV} \
    VITE_PADDLE_PRICES=${VITE_PADDLE_PRICES}
RUN pnpm build

FROM nginx:1.27-alpine
LABEL org.opencontainers.image.source="https://github.com/Bucha11/axor-control-plane" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.description="Axor frontend: the SPA, and the nginx origin that fronts the backend and proxy."
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
# The /axor location includes this file; it must exist even when no proxy token
# is configured, because nginx treats a missing include as a startup failure.
# The entrypoint hook below rewrites it from the environment at container start.
RUN touch /etc/nginx/axor-proxy-auth.conf
COPY frontend/docker-entrypoint.d/30-axor-proxy-auth.sh /docker-entrypoint.d/
RUN chmod +x /docker-entrypoint.d/30-axor-proxy-auth.sh
EXPOSE 8080
