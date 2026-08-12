import os


# The production runtime is fail-closed and requires Motor Edge configuration at
# module import. Tests replace the bridge with fakes and must never contact a Pi.
os.environ.setdefault("PIPECHECKKK_EDGE_URL", "http://127.0.0.1:9")
os.environ.setdefault("PIPECHECKKK_EDGE_TOKEN", "test-only-edge-token")
