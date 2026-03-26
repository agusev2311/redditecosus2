FROM node:22-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine

ENV SERVER_NAME=localhost \
    BACKEND_UPSTREAM=backend:5000 \
    TLS_CERT_PATH=/etc/nginx/certs/server.crt \
    TLS_KEY_PATH=/etc/nginx/certs/server.key \
    NGINX_ENVSUBST_FILTER=SERVER_NAME|BACKEND_UPSTREAM|TLS_CERT_PATH|TLS_KEY_PATH

COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80 443
