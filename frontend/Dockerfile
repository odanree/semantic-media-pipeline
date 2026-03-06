# syntax=docker/dockerfile:1
FROM node:20-alpine

WORKDIR /app

# 1. Install dependencies (cached until package.json changes)
COPY package*.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm install --legacy-peer-deps

# 2. Copy source
COPY . .

# 3. Bake NEXT_PUBLIC_* env vars at build time.
#    Use build args so each compose stack can inject its own values.
#    Defaults fall back to the main (lumen1) stack at port 8000.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_STREAM_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
ENV NEXT_PUBLIC_STREAM_URL=${NEXT_PUBLIC_STREAM_URL}

# 4. Build — NEXT_PUBLIC_* values are embedded in the JS bundle here.
RUN npm run build

EXPOSE 3000

ENV NODE_ENV=production

CMD ["npm", "start"]
