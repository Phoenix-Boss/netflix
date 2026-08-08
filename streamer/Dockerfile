# Build v2
FROM node:18-alpine
WORKDIR /app
COPY streamer/package*.json ./
RUN npm install --production
COPY streamer/ .
EXPOSE 3000
CMD ["npm", "start"]