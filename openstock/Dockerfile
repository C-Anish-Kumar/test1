# Builds OpenStock from the upstream repository at a pinned commit.
# Bump OPENSTOCK_REF (in .env) to upgrade.

FROM mongo:7 AS mongo

FROM node:22-bookworm-slim AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates libcurl4 \
 && rm -rf /var/lib/apt/lists/*
# OpenStock connects to MongoDB at module load, so `next build` needs a live database.
# A throwaway local mongod serves the build; nothing from it ends up in the final image.
COPY --from=mongo /usr/bin/mongod /usr/local/bin/mongod
WORKDIR /app

ARG OPENSTOCK_REPO=https://github.com/Open-Dev-Society/OpenStock.git
ARG OPENSTOCK_REF=18310116ec04862619fc5c746dd65947a09b29b6
RUN git init -q . \
 && git remote add origin "$OPENSTOCK_REPO" \
 && git fetch -q --depth 1 origin "$OPENSTOCK_REF" \
 && git checkout -q FETCH_HEAD \
 && rm -rf .git

RUN npm ci --no-audit --no-fund

# NEXT_PUBLIC_* values are inlined into the client bundle, so they must exist at build time.
ARG NEXT_PUBLIC_FINNHUB_API_KEY=""
ARG NEXT_PUBLIC_OPENSTOCK_DATA_MODE=cached
ENV NEXT_PUBLIC_FINNHUB_API_KEY=$NEXT_PUBLIC_FINNHUB_API_KEY \
    NEXT_PUBLIC_OPENSTOCK_DATA_MODE=$NEXT_PUBLIC_OPENSTOCK_DATA_MODE \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production
RUN mkdir -p /tmp/build-db \
 && mongod --fork --quiet --bind_ip 127.0.0.1 --dbpath /tmp/build-db --logpath /tmp/build-db.log \
 && MONGODB_URI=mongodb://127.0.0.1:27017/openstock-build \
    BETTER_AUTH_SECRET=build-only-placeholder \
    BETTER_AUTH_URL=http://localhost:3000 \
    npm run build \
 && mongod --shutdown --dbpath /tmp/build-db \
 && npm prune --omit=dev --no-audit --no-fund

FROM node:22-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app ./
USER node
EXPOSE 3000
CMD ["node_modules/.bin/next", "start"]
