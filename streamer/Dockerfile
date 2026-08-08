FROM node:18-alpine
WORKDIR /app

# Explicitly target the streamer subfolder
COPY streamer/package*.json ./
RUN npm install --production
COPY streamer/ .

EXPOSE 3000
CMD ["npm", "start"]