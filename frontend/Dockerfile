# ===== deps =====
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm config set minimumReleaseAge 0 \
 && pnpm config set strictDepBuilds false \
 && pnpm config set verifyDepsBeforeRun false \
 && pnpm install --frozen-lockfile

# ===== build =====
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS build
WORKDIR /app
RUN corepack enable \
 && pnpm config set verifyDepsBeforeRun false
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV INTERNAL_API_BASE_URL=http://backend:8080/api
RUN pnpm build

# ===== runtime =====
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
