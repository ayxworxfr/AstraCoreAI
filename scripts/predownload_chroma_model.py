"""Download ChromaDB ONNX embedding model.

Local use — run once before building to cache the model in docker/chroma_model/:
    python scripts/predownload_chroma_model.py

Docker build use (called by Dockerfile with explicit dest dir):
    python /tmp/predownload_chroma_model.py /home/appuser/.cache/chroma/onnx_models/all-MiniLM-L6-v2

Failures are non-fatal: the model will be downloaded at first runtime instead.
"""
import inspect
import os
import re
import socket
import sys
import tarfile
import urllib.request
from pathlib import Path

socket.setdefaulttimeout(600)  # 10-minute socket timeout

if len(sys.argv) > 1:
    model_dir = sys.argv[1]
else:
    model_dir = str(Path(__file__).parent.parent / "docker" / "chroma_model")

try:
    import chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 as _m

    os.makedirs(model_dir, exist_ok=True)

    existing = [f for f in os.listdir(model_dir) if not f.startswith(".")]
    if existing:
        print(f"ChromaDB ONNX model already present ({len(existing)} files), skipping download.", flush=True)
        raise SystemExit(0)

    src = inspect.getsource(_m)
    url = re.search(r"https://[^\s\"']+onnx\.tar\.gz", src).group()

    tar_path = os.path.join(model_dir, "onnx.tar.gz")
    print(f"Downloading ChromaDB ONNX model from {url} ...", flush=True)
    urllib.request.urlretrieve(url, tar_path)

    with tarfile.open(tar_path) as t:
        t.extractall(model_dir)
    os.remove(tar_path)

    print("ChromaDB ONNX model ready.", flush=True)

except SystemExit:
    raise
except Exception as e:
    print(f"Warning: model pre-download skipped ({e})", flush=True)
