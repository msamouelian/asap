FROM python:3.12-slim

# Non-root user — uid 1000 is safe for most production clusters.
RUN useradd --uid 1000 --create-home --shell /bin/bash asap

WORKDIR /app

# libgl1/libglib2.0-0/libxcb1: OS libraries OpenCV (docling's PDF pipeline)
# links against; python:3.12-slim ships none of them.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxcb1 \
 && rm -rf /var/lib/apt/lists/*

# Copy only the asapdocworker package tree. Build context must be the
# workspace root so this path resolves (see scripts/docker-build-push.sh).
COPY packages/asapdocworker packages/asapdocworker/

RUN pip install --no-cache-dir "pip>=26.1" wheel

# CPU-only torch FIRST, so docling's torch requirement is already satisfied —
# the default wheel drags in ~5 GB of NVIDIA CUDA packages this CPU worker
# can never use. When a GPU cluster arrives, swap this index for the CUDA one.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir -e packages/asapdocworker/

# Pre-bake the models docling and the chunker download on first use (layout /
# table-structure models + the bge tokenizer), so Jobs start working
# immediately and run without internet access (HF_HUB_OFFLINE=1 at runtime).
# DOCLING_ARTIFACTS_PATH must be an explicit neutral path: this RUN executes
# as root, so anything cached under a HOME directory lands in /root and is
# invisible to the runtime user — which broke PDF processing until models
# were pinned here.
ENV HF_HOME=/home/asap/.cache/huggingface
ENV DOCLING_ARTIFACTS_PATH=/opt/docling-models
RUN python -c "\
from pathlib import Path; \
from docling.utils.model_downloader import download_models; \
download_models(output_dir=Path('/opt/docling-models')); \
from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('BAAI/bge-small-en-v1.5')" \
 && chmod -R a+rX /opt/docling-models \
 && chown -R asap:asap /home/asap/.cache

USER asap

ENTRYPOINT ["python", "-m", "asapdocworker.main"]
