from pathlib import Path


def ai_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "3d_gsplat").is_dir():
            return parent
    raise AssertionError("ai project root containing 3d_gsplat/ was not found")


def test_user_implemented_ai_is_grouped_without_gitlab_3d() -> None:
    root = ai_root()
    agent = root / "AIAgent"

    assert (agent / "pyproject.toml").is_file()
    assert (agent / "src" / "pipecheckkk_ai" / "api.py").is_file()
    assert (agent / "src" / "pipecheckkk_ai" / "progress_summary.py").is_file()
    assert (
        agent
        / "src"
        / "pipecheckkk_ai"
        / "decision"
        / "qwen2_5_vl_3b_instruct_awq"
        / "adapter.py"
    ).is_file()
    assert not (agent / "src" / "pipecheckkk_ai" / "perception" / "yolo.py").exists()
    assert not (agent / "src" / "pipecheckkk_ai_v3").exists()
    assert (agent / "src" / "pipecheckkk_ai" / "navigation.py").is_file()
    assert not (agent / "src" / "pipecheckkk_ai_v2").exists()
    assert not (agent / "src" / "pipecheckkk_ai_v4").exists()
    assert not (agent / "models" / "navigation-v2").exists()
    assert not (agent / "scripts" / "train_navigation_v2.py").exists()
    assert (agent / "tests" / "test_progress_summary.py").is_file()
    assert not (agent / "tests" / "test_yolo.py").exists()
    assert not (agent / "models" / "yolo11n.pt").exists()

    assert (root / "3d_gsplat" / "README.md").is_file()
    assert not (agent / "3d_gsplat").exists()
    assert not (root / "YOLO").exists()
