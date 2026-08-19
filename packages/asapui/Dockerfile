# ── Stage 1: build ───────────────────────────────────────────────────────────
FROM node:24-alpine AS builder
WORKDIR /app

# Install dependencies before copying source so this layer is cached.
COPY packages/asapui/package.json packages/asapui/package-lock.json ./
RUN npm ci

# Copy source and build the SPA.
COPY packages/asapui/ ./
RUN npm run build

# ── Stage 2: serve ───────────────────────────────────────────────────────────
FROM nginx:1.27-alpine
RUN apk upgrade --no-cache \
    && apk del --no-cache \
       curl \
       nginx-module-image-filter \
       nginx-module-xslt \
       nginx-module-njs \
       nginx-module-geoip
COPY --from=builder /app/build /usr/share/nginx/html
COPY packages/asapui/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
