# AgentCore Runtime (MCP protocol) image: linux/arm64, Streamable HTTP on 0.0.0.0:8000/mcp.
# Build from repo root: docker buildx build --platform linux/arm64 -f runtime/mcp_specialist/Dockerfile .
FROM public.ecr.aws/docker/library/python:3.13-slim
WORKDIR /app
COPY runtime/mcp_specialist/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY foundations ./foundations
COPY foundation_harness ./foundation_harness
COPY backend ./backend
COPY runtime ./runtime
ARG IMAGE_BUILD=unknown
ENV EXECUTION_MODE=local IMAGE_BUILD=${IMAGE_BUILD} PYTHONUNBUFFERED=1
RUN chmod -R a+rX /app
USER 10001
EXPOSE 8000
CMD ["python", "-m", "runtime.mcp_specialist.agentcore"]
