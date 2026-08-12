# 빌드 컨텍스트는 backend/ 다 (compose 의 context: ../../backend). priority_engine 의
# 산출물을 이미지에 구워야 하는데 docker 는 컨텍스트 밖을 COPY 할 수 없어서 한 단계
# 올렸다. 그래서 아래 경로는 모두 cloud_api/ 로 시작한다.

FROM python:3.12-slim

WORKDIR /app

# WeasyPrint(보고서 PDF 렌더)가 요구하는 텍스트 스택과 한글 폰트.
# 폰트가 없으면 PDF의 모든 한글이 빈 사각형으로 나온다.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libffi8 \
        fonts-nanum \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

COPY cloud_api/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# 우선순위 엔진 산출물 5.8MB. 바인드 마운트로 붙이지 않고 굽는 이유:
#   - 상대 경로 마운트는 compose 파일 위치 기준으로 풀려서, Jenkins 워크스페이스
#     (도커 볼륨 안, 호스트에 없음)에서 배포하면 빈 디렉터리가 붙는다.
#     2026-08-03에 경계 GeoJSON 이 이 이유로 404가 났다.
#   - 이 산출물은 git 이 추적하므로 바뀌면 어차피 커밋이 생긴다. 그때 이미지를
#     다시 만드는 것이 맞는 동작이다.
# app 코드보다 덜 자주 바뀌므로 앞에 두어 캐시를 살린다.
COPY priority_engine/data ./priority_data
ENV PRIORITY_DATA_DIR=/app/priority_data

COPY cloud_api/app ./app
COPY cloud_api/scripts ./scripts

# 배포된 커밋을 /api/health 로 노출하기 위한 값. 커밋마다 바뀌므로 캐시가 깨져도
# 손해가 없도록 맨 뒤에 둔다. CI 가 --build-arg GIT_COMMIT=... 로 넘긴다.
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=${GIT_COMMIT}

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
