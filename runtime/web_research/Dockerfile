FROM public.ecr.aws/docker/library/python:3.13-slim
WORKDIR /app
COPY runtime/web_research/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY foundations ./foundations
COPY tools ./tools
COPY backend/schemas.py ./backend/schemas.py
COPY backend/__init__.py ./backend/__init__.py
COPY runtime ./runtime
USER 10001
EXPOSE 8080
CMD ["python", "-m", "runtime.web_research.main"]
