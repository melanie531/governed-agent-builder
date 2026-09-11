import os
import uvicorn

if __name__ == "__main__":
    if os.getenv("HOST", "127.0.0.1") not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("Demo server refuses non-loopback binding")
    uvicorn.run("backend.app:create_app", factory=True, host="127.0.0.1", port=int(os.getenv("PORT", "5187")), workers=1, access_log=False)
