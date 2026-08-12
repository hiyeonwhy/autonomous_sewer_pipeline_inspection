# --- build stage ---
FROM node:20-alpine AS build
WORKDIR /app

# package.json / package-lock.json 먼저 COPY해서 의존성 레이어를 캐시한다.
COPY package.json package-lock.json ./
RUN npm ci

# .env.production은 git에 이미 커밋되어 있고 VITE_API_BASE_URL=/api(상대경로)라
# API 주소용 build ARG 는 필요 없다. 다만 VITE_* 는 빌드 타임에 번들로 박히므로
# 주소를 바꾸려면 이미지를 다시 만들어야 한다.
COPY . .
RUN npm run build

# 배포된 커밋을 컨테이너 밖에서 확인할 수 있어야 스모크 테스트가 "새 버전이 떴는지"를
# 판정할 수 있다. CI가 --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) 로 넘긴다.
# 커밋마다 값이 바뀌어 캐시가 깨지므로 build 뒤에 둔다.
ARG GIT_COMMIT=unknown
RUN printf '{"commit":"%s","built_at":"%s"}\n' \
        "$GIT_COMMIT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    > dist/build-info.json

# --- serve stage ---
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
