from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Sewer Inspection Cloud API"

    # 배포된 커밋. Dockerfile 이 빌드 인자로 받아 환경변수로 굽고, /api/health 가
    # 그대로 돌려준다. 배포본이 저장소보다 뒤처졌는지를 밖에서 판정하는 근거다.
    git_commit: str = "unknown"

    mongodb_url: str
    mongodb_db_name: str = "sewer_inspection"
    media_root: str = "/app/media"
    # Laptop Agent의 telemetry/frame/log 쓰기 전용 공유 키. Dashboard GET은 공개다.
    agent_publish_key: str = ""

    # 우선순위 엔진 산출물(JSON/GeoJSON) 위치. 저장소에서는 cloud_api 와
    # priority_engine 이 backend/ 아래 형제라 상대 경로로 찾을 수 있지만,
    # 컨테이너에는 cloud_api 만 들어오므로 그 레이아웃이 성립하지 않는다.
    # 배포 환경에서는 마운트 지점을 넣고, 비워 두면 저장소 레이아웃을 쓴다.
    priority_data_dir: str = ""

    # 보고서 서술형 요약을 만드는 LLM. 키가 없으면 요약은 규칙 기반으로 돌아가고
    # PDF는 그대로 나온다 — 설정 누락이 다운로드를 막지는 않는다.
    llm_enabled: bool = True
    llm_api_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    # 사진 판독용 모델. 비워 두면 llm_model을 그대로 쓴다.
    llm_vision_model: str = ""
    llm_timeout_s: float = 60.0
    llm_describe_images: bool = True
    # 사진 판독은 결함 1건당 호출 1회라 지연이 결함 수에 비례한다. 상한을 둔다.
    llm_max_vision_defects: int = 8
    # 사진 1장을 이 크기 이하로 줄여 보낸다. SSAFY GMS는 요청 본문이 약 100KB를
    # 넘으면 400을 내고, base64가 약 1.37배로 부풀린다.
    llm_image_budget_bytes: int = 60 * 1024

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()