# Web image. Build from web/:  docker build -t audit-web web
FROM node:24-slim AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# next.config.ts needs the typescript dev dependency at start, so node_modules is kept whole.
# Build-time placeholders only: the proxy reads the real values at request time on the server.
ENV API_BASE_URL=http://127.0.0.1:8000 AUDIT_API_KEY=build-placeholder NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-slim
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN useradd --system --uid 10001 --create-home app
WORKDIR /web
COPY --from=build --chown=app:app /web/package.json ./
COPY --from=build --chown=app:app /web/node_modules ./node_modules
COPY --from=build --chown=app:app /web/.next ./.next
COPY --from=build --chown=app:app /web/next.config.ts /web/tsconfig.json ./
USER app
EXPOSE 3000
CMD ["npx", "next", "start", "-H", "0.0.0.0", "-p", "3000"]
