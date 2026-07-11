# Frontend image: build the SPA + demo with pnpm, serve it (and reverse-proxy
# /v1 -> backend, /axor -> proxy) with nginx. No private deps — builds anywhere.
FROM node:22-slim AS build
WORKDIR /app/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend ./
# Checkout link for the Pricing CTA (launch-readiness §5): bake a Stripe
# payment link in at build time — no code change, just a build arg.
ARG VITE_CHECKOUT_URL=""
ENV VITE_CHECKOUT_URL=${VITE_CHECKOUT_URL}
RUN pnpm build

FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
EXPOSE 8080
