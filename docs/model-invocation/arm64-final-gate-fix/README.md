# ARM64 evidence re-bound to post-final-gate-fix source

The final-deadline-gate fix (`c188020`) modified `foundation_harness/transport.py`, so
prior ARM64 evidence (source digest `491b68c9…`, package `46926e7e…`) is superseded and
NOT reused. `f33c7ac` was 哥哥-verified genuine but predates this fix and does NOT cover
this boundary. Fresh rebuild + offline run from the CURRENT post-fix source.

## Rebuild (post-fix source)
- Source commit: `c188020b66acf0fdae037e95a576eadaf76224aa` (additive off tip `37e1559`)
- NEW source digest: `463c79cc48e652ac12b29283523b271c361f35438a3c0749d96228dc577179ee`  (was `491b68c9…`)
- NEW package sha256: `47cf41e40a49c5eb2d5fa57166be69813d09db89e2e75cdc951334a0c5f7caa8`  (was `46926e7e…`), 27,513,837 bytes, base linux-arm64-python3.13-locked, 6 native aarch64 `.so`.
- Extracted `foundation_harness/transport.py` byte-matches source AND contains `_FinalDeadlineTransport` (the fix).

## Fresh offline ARM64 run (EMULATED — qemu binfmt, NOT native, NOT AgentCore acceptance)
- Image: `arm64v8/python@sha256:c33ba31a…` (id `sha256:b00eb95f…`)
- Flags: `--platform linux/arm64 --network none --read-only --cap-drop ALL --security-opt no-new-privileges --memory 512m --cpus 1 --tmpfs /tmp:size=32m`, `python -I /probe.py`, read-only mounts.
- In-container `uname -m` = `aarch64`; Python `3.13.15`; packaged httpx `0.28.1`; **exit code 0**.
- Cases (send-count on real MockTransport dispatch):
  - build_delay 61 (deadline crossed during build) → **0** calls, rejected
  - build_delay 3 (valid) → **1** call, 7s phase timeouts
  - **crossed_after_prep_before_dispatch (the fixed defect window)** → **0** calls, rejected
- No network, no credentials, no provider/IAM/cloud calls, 0 inference.

Emulated Linux ARM64 only. Base synthetic identity / unadmitted; production identity,
pricing, budget, permission approval remain OPEN. `deploy_ready:false`, `production_ready:false`.
NOT AgentCore activation acceptance.
