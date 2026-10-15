FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG VITE_SWAP_URL=
ARG VITE_SWAP_NATIVE=ETH
ENV VITE_SWAP_URL=$VITE_SWAP_URL VITE_SWAP_NATIVE=$VITE_SWAP_NATIVE
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
