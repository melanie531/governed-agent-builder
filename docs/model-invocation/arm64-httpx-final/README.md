# Final HTTPX preparation deadline fix

Implementation154a811827a0c9d588e22fdbbadd8560aefd2ae9 adds only a request hook in IAMTransport._send plus regression tests; codec unchanged. HTTPX hook runs after request build/auth preparation and before transport dispatch. It checks absolute capture deadline and updates per-phase timeout from remaining duration. New tests failed2/2 on prior source,then focused regression78passed,2existing warnings,1.85s.

Fresh locked Linux ARM64 Python3.13 base ZIP built from that exact commit; source digest,ZIP hash,source tree and image identity in receipt.json. Packaged transport bytes matched implementation; extracted bytes checked against ZIP after execution. NOT the old f33c7ac package.

Executed in pinned existing Docker ARM image via QEMU binfmt on x86_64: --platform linux/arm64 --network none --read-only --cap-drop ALL --security-opt no-new-privileges --memory512m --cpus1,tmpfs32m; exact extracted artifact and probe mounted read-only,python -I /probe.py. Exit0,aarch64,Python3.13.15,packaged httpx0.28.1. Actual HTTPX build_request executes; patched build adds simulated elapsed time,MockTransport counts final sends. Construction crossing deadline:0 transport calls. Valid construction3s:1 mock call,all phase timeouts7s. This tests the newly identified stage,not merely credential/signature delays.

probe.py/stdout.json/stderr.log are actual run evidence. No live credentials,network/provider calls,IAM/cloud changes. Emulated Linux ARM64 only,not AgentCore field acceptance. Base still synthetic identity/unadmitted; production identity,pricing,budget and permission approval remain open.
