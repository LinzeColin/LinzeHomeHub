# LinzeHomeHub — Golden Path 生产镜像 (静态 Vite 站, 替代无人维护的手动 wrangler deploy)
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# 安全: 基底镜像补丁 (Trivy 实测 35 HIGH/2 CRIT -> 0/0)
RUN apk upgrade --no-cache
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
