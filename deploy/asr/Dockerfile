# Local speech recognition for Jarvis (server.py). The model is NOT in the image: fetch_model.sh puts it in $BODY_ROOT/models/asr/
# on the host, mounted read-only at /models. Build on Ali: docker build -t body-asr:0.1.0 deploy/asr
FROM python:3.12.15-slim
RUN pip install --no-cache-dir sherpa-onnx==1.13.8 sherpa-onnx-core==1.13.8 numpy==2.3.4 \
 && useradd --system --uid 10010 asr
COPY server.py /app/server.py
USER asr
ENV PYTHONUNBUFFERED=1 ASR_MODEL_DIR=/models
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
CMD ["python", "/app/server.py"]
