FROM node:24-slim
WORKDIR /app
COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
RUN npm ci --no-fund
COPY apps/web ./apps/web
COPY packages ./packages
RUN npm run build
EXPOSE 3000
CMD ["npm", "run", "start"]
