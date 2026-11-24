FROM node:24-bookworm-slim
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
ARG INTERNAL_API_URL=http://backend:8000
ENV INTERNAL_API_URL=$INTERNAL_API_URL
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build && npm prune --omit=dev && chown -R node:node .next
USER node
EXPOSE 3000
CMD ["npm", "run", "start"]
