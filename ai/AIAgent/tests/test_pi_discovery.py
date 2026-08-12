from __future__ import annotations

import pytest

from pipecheckkk_ai.pi_discovery import discover_pi, hosts_from_ip_json


def test_hosts_from_ip_json_returns_current_wifi_subnet_without_self() -> None:
    payload = [
        {
            "ifname": "wlo1",
            "addr_info": [
                {"family": "inet", "local": "192.168.43.76", "prefixlen": 24, "scope": "global"}
            ],
        }
    ]

    hosts = hosts_from_ip_json(payload)

    assert "192.168.43.1" in hosts
    assert "192.168.43.169" in hosts
    assert "192.168.43.76" not in hosts


def test_discover_pi_requires_same_host_camera_and_motor_edge() -> None:
    health = {
        "192.168.43.10": ({"ok": True, "service": "pipecheckkk-edge-motor"}, None),
        "192.168.43.20": (None, {"ok": True, "camera": "Brio 100"}),
        "192.168.43.169": (
            {"ok": True, "service": "pipecheckkk-edge-motor", "armed": False},
            {"ok": True, "camera": "Brio 100", "frame_age_s": 0.01},
        ),
    }

    assert discover_pi(list(health), probe=lambda host: health[host]) == "192.168.43.169"


def test_discover_pi_fails_closed_for_ambiguous_hosts() -> None:
    both = (
        {"ok": True, "service": "pipecheckkk-edge-motor", "armed": False},
        {"ok": True, "camera": "Brio 100", "frame_age_s": 0.01},
    )

    with pytest.raises(RuntimeError, match="exactly one"):
        discover_pi(["192.168.43.10", "192.168.43.11"], probe=lambda _host: both)
