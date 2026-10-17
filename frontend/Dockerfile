FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
# npm's cache lives in a BuildKit cache mount, not in the image layer: on this 10 GB box the dependency
# tree (AppKit + wagmi + viem) otherwise pushes `npm ci` and the image export past the free space.
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY . .
ARG VITE_SWAP_URL=
ARG VITE_SWAP_NATIVE=ETH
ARG VITE_WC_PROJECT_ID=
ENV VITE_SWAP_URL=$VITE_SWAP_URL VITE_SWAP_NATIVE=$VITE_SWAP_NATIVE VITE_WC_PROJECT_ID=$VITE_WC_PROJECT_ID
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
