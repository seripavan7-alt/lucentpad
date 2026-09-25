# Dev server for the LucentPad dashboard. Build context: dashboard/
FROM node:24-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ENV LUCENTPAD_API_URL=http://api:8000
EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host"]
