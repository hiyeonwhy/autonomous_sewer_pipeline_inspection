#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

JsonDict = dict[str, Any]
RequestFn = Callable[..., JsonDict]
ACTIVE_STATES = {"RUNNING", "PAUSED", "RETURNING"}


def request_json(
    api: str,
    path: str,
    *,
    method: str = "GET",
    payload: JsonDict | None = None,
) -> JsonDict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{api.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - local operator API
            return json.load(response)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Agent API 연결 실패: {error.reason}") from error


class OperatorClient:
    def __init__(
        self,
        api: str = "http://127.0.0.1:8000",
        *,
        request: RequestFn | None = None,
    ) -> None:
        self.api = api.rstrip("/")
        self._request = request or (
            lambda path, *, method="GET", payload=None: request_json(
                self.api,
                path,
                method=method,
                payload=payload,
            )
        )

    def start(self, area: str) -> JsonDict:
        return self._request(
            "/api/mission/start",
            method="POST",
            payload={"area": area},
        )

    def stop(self) -> JsonDict:
        return self._request("/api/mission/complete", method="POST", payload={})

    def reset(self) -> JsonDict:
        return self._request("/api/mission/reset", method="POST", payload={})

    def status(self) -> JsonDict:
        return {
            "mission": self._request("/api/mission"),
            "drive": self._request("/api/drive"),
            "edge": self._request("/api/edge"),
            "navigation": self._request("/api/navigation"),
        }

    def safe_shutdown(self) -> None:
        mission = self._request("/api/mission")
        if str(mission.get("state", "")).upper() in ACTIVE_STATES:
            self.stop()


def print_status(status: JsonDict) -> None:
    mission = status["mission"]
    drive = status["drive"]
    edge = status["edge"]
    navigation = status["navigation"]
    observation = drive.get("latestObservation") or drive.get("latest_observation") or {}
    command = drive.get("latestCommand") or drive.get("latest_command") or {}
    imu = observation.get("imuReading") or observation.get("imu_reading") or {}
    print(
        json.dumps(
            {
                "mission": mission.get("state"),
                "navigation": navigation.get("activeVersion"),
                "armed": edge.get("armed"),
                "edgeReason": edge.get("reason"),
                "distanceM": observation.get("ultrasonicDistanceM")
                or observation.get("ultrasonic_distance_m"),
                "imuPhase": imu.get("phase"),
                "action": command.get("action"),
                "left": command.get("leftSpeed") or command.get("left_speed"),
                "right": command.get("rightSpeed") or command.get("right_speed"),
            },
            ensure_ascii=False,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="PipeCheckkk V2 dashboard-free operator terminal")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--area", default="터미널 V2 주행 테스트")
    args = parser.parse_args()
    client = OperatorClient(args.api)

    print("PipeCheckkk V2 운영 터미널")
    print("명령: start | status | stop | reset | q")
    print("주의: start 입력 즉시 Pi가 arm되고 실제 모터가 움직입니다.")
    try:
        print_status(client.status())
        while True:
            command = input("command> ").strip().lower()
            try:
                if command == "start":
                    print(json.dumps(client.start(args.area), ensure_ascii=False))
                elif command == "status":
                    print_status(client.status())
                elif command == "stop":
                    print(json.dumps(client.stop(), ensure_ascii=False))
                elif command == "reset":
                    print(json.dumps(client.reset(), ensure_ascii=False))
                elif command in {"q", "quit", "exit"}:
                    client.safe_shutdown()
                    print("STOP+disarm 확인 후 종료합니다.")
                    return 0
                elif command:
                    print("지원 명령: start | status | stop | reset | q")
            except RuntimeError as error:
                print(f"ERROR: {error}")
    except (KeyboardInterrupt, EOFError):
        print("\n종료 요청: RUNNING이면 STOP+disarm합니다.")
        try:
            client.safe_shutdown()
        except RuntimeError as error:
            print(f"ERROR: 안전 종료 확인 실패: {error}")
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
