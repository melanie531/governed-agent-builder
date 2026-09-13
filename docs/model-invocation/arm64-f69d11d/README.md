# Rebuilt f69d11d ARM64 base, NOT admitted

Fixed source f69d11d2c2be26f12e9254a87aa54d96b4c85560. Fresh hash-locked aarch64-manylinux2014 Python3.13 dependencies installed; no changes to codec or product source. Source digest and new ZIP SHA256 in receipt.json. ZIP contains8 AArch64 native libraries; packaged transport.py byte-matches the fixed commit. After execution, every extracted file byte-matches ZIP.

Actual execution used existing Docker/QEMU binfmt on x86_64, image pinned by content ID from receipt. Container network none,read-only,cap-drop ALL,no-new-privileges,memory512m,CPU1,tmpfs32m. Mounted exact extracted ZIP at /artifact and probe.py at /probe.py, both read-only; ran python -I /probe.py. Actual container architecture aarch64/Python3.13.15,exit0. Native dependency import,manifest roundtrip,codec+synthetic signed transport passed. New packaged deadline guard tested independently for slow credential fetch and slow signing:0 wire sends in each; deadline context reset verified. stdout/stderr preserved.

This is emulated Linux ARM64,not native ARM hardware or AgentCore field verification. Synthetic response identity,unadmitted base,deploy_ready=false. No IAM changes/real credentials/model calls/cloud resources. Old4e79c12b artifact remains historical and is not relabeled. Production identity/pricing/budget/admission and real Studio acceptance remain unresolved.
