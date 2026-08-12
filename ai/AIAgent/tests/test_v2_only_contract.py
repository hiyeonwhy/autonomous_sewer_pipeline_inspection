from pathlib import Path

from fastapi.testclient import TestClient

from pipecheckkk_ai.api import create_app
from pipecheckkk_ai.navigation import NavigationVersion


AGENT_ROOT = Path(__file__).resolve().parents[1]


def test_v2_is_the_only_navigation_runtime() -> None:
    assert list(NavigationVersion) == [NavigationVersion.V2]
    assert not (AGENT_ROOT / "src" / "pipecheckkk_ai_v3").exists()


def test_navigation_api_exposes_only_v2() -> None:
    client = TestClient(create_app())

    response = client.get("/api/navigation")

    assert response.status_code == 200
    assert response.json() == {
        "activeVersion": "v2",
        "label": "V2 Depth + Pi IMU",
    }
    assert client.put("/api/navigation", json={"version": "v1"}).status_code == 405
    assert client.post("/api/drive/step", json={}).status_code == 404
    assert client.post("/api/drive/camera-step", json={}).status_code == 404
    assert client.put("/api/navigation", json={"version": "v3"}).status_code == 405


def test_yolo_surface_is_read_only_visualization_only() -> None:
    app = create_app()
    route_paths = {route.path for route in app.routes}

    assert not (AGENT_ROOT / "src" / "pipecheckkk_ai" / "perception" / "yolo.py").exists()
    assert "/api/vision/yolo-frame.jpg" in route_paths
    assert "/api/vision/yolo" not in route_paths
    assert "/api/drive/step" not in route_paths
    assert "/api/drive/camera-step" not in route_paths
