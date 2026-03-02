# syntax=docker/dockerfile:1
FROM node:20-alpine

WORKDIR /app

# 1. Install dependencies (cached until package.json changes)
COPY package*.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm install --legacy-peer-deps

# 2. Copy source and build (only rebuilds when code changes)
COPY . .
RUN npm run build

EXPOSE 3000

ENV NODE_ENV=production

CMD ["npm", "start"]
