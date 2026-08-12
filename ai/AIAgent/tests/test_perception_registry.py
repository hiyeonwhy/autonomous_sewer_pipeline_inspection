from pipecheckkk_ai.perception.registry import create_perception_provider


def test_v2_perception_provider_is_depth_freespace() -> None:
    provider = create_perception_provider()

    assert provider.name == "depth-freespace"
