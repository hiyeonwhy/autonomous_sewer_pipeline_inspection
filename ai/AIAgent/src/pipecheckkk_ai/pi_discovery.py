from __future__ import annotations

import ipaddress
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable
from urllib.request import urlopen

HealthPair = tuple[dict[str, object] | None, dict[str, object] | None]


def hosts_from_ip_json(payload: list[dict[str, object]]) -> list[str]:
    hosts: set[str] = set()
    own: set[str] = set()
    networks: set[ipaddress.IPv4Network] = set()
    for interface in payload:
        addr_info = interface.get("addr_info", [])
        if not isinstance(addr_info, list):
            continue
        for address in addr_info:
            if not isinstance(address, dict):
                continue
            if address.get("family") != "inet" or address.get("scope") != "global":
                continue
            local = str(address.get("local", ""))
            prefix = int(address.get("prefixlen", 32))
            ip = ipaddress.IPv4Address(local)
            network = ipaddress.IPv4Network(f"{ip}/{prefix}", strict=False)
            if not ip.is_private or network.num_addresses > 1024:
                continue
            own.add(str(ip))
            networks.add(network)
    for network in networks:
        hosts.update(str(host) for host in network.hosts())
    return sorted(hosts - own, key=ipaddress.ip_address)


def probe_health(host: str, timeout_s: float = 0.35) -> HealthPair:
    def read(port: int) -> dict[str, object] | None:
        try:
            with urlopen(f"http://{host}:{port}/health", timeout=timeout_s) as response:
                if response.status != 200:
                    return None
                payload = json.load(response)
                return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    return read(8765), read(8081)


def discover_pi(
    hosts: Iterable[str],
    *,
    probe: Callable[[str], HealthPair] = probe_health,
) -> str:
    candidates: list[str] = []
    host_list = list(hosts)
    with ThreadPoolExecutor(max_workers=min(80, max(1, len(host_list)))) as executor:
        for host, (edge, camera) in zip(host_list, executor.map(probe, host_list)):
            edge_ok = bool(edge and edge.get("ok") is True and edge.get("service") == "pipecheckkk-edge-motor")
            camera_ok = bool(camera and camera.get("ok") is True and camera.get("camera"))
            if edge_ok and camera_ok:
                candidates.append(host)
    if len(candidates) != 1:
        raise RuntimeError(f"expected exactly one Pi with camera+motor Edge, found {candidates}")
    return candidates[0]
