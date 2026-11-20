FROM node:22-bookworm-slim AS build
WORKDIR /site
COPY package*.json ./
RUN npm ci --include=dev --no-audit --no-fund
COPY . .
RUN npm run build -- --mode public && test -s dist/mobile.html && test ! -e dist/demo

FROM caddy:2-alpine@sha256:24ec7b05bc532c7bb57f727030c5056e257ff7138b4b50460306819ff79362bd
COPY --from=build /site/dist/ /srv/www/
