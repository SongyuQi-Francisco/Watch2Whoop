# The scoring algorithms live in ../packages/openbodyscore (a path dependency, issue #115). They come in as the named
# build context `openbodyscore`: compose.yaml sets it (additional_contexts); by hand, from the repository root:
#   docker build --build-context openbodyscore=packages/openbodyscore body_engine
# Without it the empty stage below is used (never an image of that name from a registry) and the check fails the build.
FROM scratch AS openbodyscore

FROM python:3.12.15-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
# At most 2 glibc malloc arenas: by default every thread that allocates may get its own arena (8 per CPU), and the API runs
# requests in a pool of ~40 threads, so freed memory stayed spread over many arenas and the resident size only grew
# (docs/api-memory.md). Costs a little lock contention, nothing measurable at our load.
ENV MALLOC_ARENA_MAX=2
# uv.lock refers to ../packages/openbodyscore, i.e. /packages/openbodyscore next to /app
COPY --from=openbodyscore . /packages/openbodyscore
RUN test -f /packages/openbodyscore/pyproject.toml \
    || { echo "missing build context: add --build-context openbodyscore=packages/openbodyscore (see the top of this file)" >&2; exit 1; }
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
EXPOSE 8100
CMD ["sh", "-c", "body-engine migrate && uvicorn body_engine.api:app --host 0.0.0.0 --port 8100"]
