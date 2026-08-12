from fastapi import APIRouter, HTTPException, Request, Response, status

from app.core.operator_auth import OperatorCodeError, verify_operator_passcode
from app.domain.resources.schema import (
    OperatorCodeRequest,
    OperatorResponse,
    RobotResponse,
)

router = APIRouter(prefix="/api", tags=["resources"])


@router.get("/robots", response_model=list[RobotResponse])
async def get_robots(request: Request) -> list[RobotResponse]:
    database = request.app.state.database
    documents = await database.robots.find({}).to_list(length=100)
    return [RobotResponse.model_validate(document) for document in documents]


@router.get("/operators", response_model=list[OperatorResponse])
async def get_operators(request: Request) -> list[OperatorResponse]:
    database = request.app.state.database
    documents = await database.operators.find({}).to_list(length=100)
    # 확인 코드(소금·해시)는 응답 모델에 없으므로 밖으로 나가지 않는다.
    return [OperatorResponse.model_validate(document) for document in documents]


@router.post("/operators/{operator_id}/verify-code", status_code=status.HTTP_204_NO_CONTENT)
async def verify_operator_code(
    operator_id: int,
    payload: OperatorCodeRequest,
    request: Request,
) -> Response:
    """확인 코드가 맞는지만 알려 준다.

    로봇 제어(일시 정지·다시 시작·점검 종료·긴급 정지)는 Agent 로 나가는 명령이라
    이 서버를 거치지 않는다. 그래서 화면이 명령을 보내기 전에 여기로 물어본다.
    화면 안에서 비교하면 개발자 도구로 코드를 읽어 갈 수 있다.
    """
    database = request.app.state.database
    try:
        await verify_operator_passcode(
            database, operator_id=operator_id, code=payload.code
        )
    except OperatorCodeError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(error)
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
