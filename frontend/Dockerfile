# ==== Stage 1: deps ====
FROM node:18-alpine AS deps
WORKDIR /app
COPY package.json ./
# Use npm install (not ci) since we don't ship a lockfile
RUN npm install --no-audit --no-fund

# ==== Stage 2: builder ====
FROM node:18-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ==== Stage 3: runner ====
FROM node:18-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Next.js standalone output already bundles the minimal runtime
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000
CMD ["node", "server.js"]
