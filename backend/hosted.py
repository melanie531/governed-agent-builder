"""Explicit hosted launcher. Viewer HTTPS and private origin are infrastructure obligations."""
import os
import uvicorn

if __name__ == "__main__":
    if os.getenv("HOSTED_PREVIEW") != "1" or os.getenv("DEMO_MODE") == "1":
        raise SystemExit("Use HOSTED_PREVIEW=1 without DEMO_MODE for this launcher")
    if not os.getenv("STATE_PATH", "").startswith("/data/"):
        raise SystemExit("Hosted STATE_PATH must use the mounted persistent /data volume")
    # No forwarded host/scheme/identity trust. PUBLIC_URL is an explicit setting.
    # Bind loopback for a same-host private HTTP proxy; no public listener is created here.
    uvicorn.run("backend.app:create_app", factory=True, host="127.0.0.1",
                port=5187, workers=1, proxy_headers=False, access_log=False)
