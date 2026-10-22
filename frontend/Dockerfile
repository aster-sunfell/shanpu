# ===== package manager =====
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS tooling
WORKDIR /app
COPY package.json ./
# Read the exact project version; do not let Corepack resolve the latest release.
RUN npm install --global "$(node -p 'require("./package.json").packageManager')" \
 && test "$(pnpm --version)" = "$(node -p 'require("./package.json").packageManager.split("@")[1]')"

# ===== deps =====
FROM tooling AS deps
COPY pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# ===== build =====
FROM deps AS build
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
