# Fresh ARM64 offline rebuild — post-f69d11d (transport.py changed)

`f69d11d` ("enforce capture deadline after signing and explicit scoped admission")
modified `foundation_harness/transport.py`, so prior ARM64 offline-run evidence is
treated as STALE and NOT reused. This directory is a fresh rebuild + run from the
CURRENT source at tip `37e155985d1527e0bfffb6b375a4a3a658b58041`.

## Rebuild (from current source)
- Source commit: `37e155985d1527e0bfffb6b375a4a3a658b58041`
- New source digest (SOURCES tree, incl. transport.py): `491b68c9df89b516c59fd7955d3e9197611f43fc1f3004d240749cea000da1c4`
- New package sha256 (base, locked linux-arm64-python3.13): `46926e7e2a13d2195e3fb596fd5fe13faa5d407777738a4dbedb4729a3c9bb16`
- Build: `uv pip install --target <deps> --python-version 3.13 --python-platform aarch64-manylinux2014 --only-binary :all: --require-hashes --no-deps -r runtime/custom_foundation/requirements.lock` → deterministic ZIP via `scripts/package_foundation.package(..., mode='base', dependencies=<deps>)`.
- Extracted `foundation_harness/transport.py` byte-matches current source AND contains `capture_deadline` (post-f69d11d). 6 native aarch64 `.so` in artifact.

NOTE: the package sha256 reproduces identically to the prior post-f69d11d build
because SOURCES + locked wheels are byte-identical AND the packager is deterministic
(fixed ZipInfo timestamps, `--require-hashes`). This is an INDEPENDENT fresh
rebuild+run — the digest equality is a reproducibility property, not a reuse of the
old hash/log.

## Fresh offline ARM64 run (EMULATED — qemu binfmt, NOT native, NOT AgentCore acceptance)
- Image: `arm64v8/python@sha256:c33ba31a4f8fc187a1ec9f33ef63945c29e9eaa74c47542894f26db67a4156d6`
- Image id: `sha256:b00eb95f27b4a7c666c7b99764515e147ab19ff26451123346f40c23a031498a`
- Flags: `--platform linux/arm64 --network none --read-only --cap-drop ALL --security-opt no-new-privileges --memory 512m --cpus 1 --tmpfs /tmp:size=32m`, `python -I /probe.py`, artifact + probe mounted read-only.
- In-container `uname -m` = `aarch64`; Python `3.13.15`; packaged httpx `0.28.1`; **exit code 0**.
- Result: real `httpx.build_request` runs; build crossing the capture deadline → **0** transport calls (deadline_rejected); valid 3s build → **1** MockTransport call, all phase timeouts 7s. No network, no live credentials, no provider/IAM/cloud calls, 0 inference.

Emulated Linux ARM64 only. Base is synthetic identity / unadmitted; production identity, pricing, budget, and permission approval remain OPEN. `deploy_ready:false`, `production_ready:false`. This is NOT AgentCore activation acceptance.
