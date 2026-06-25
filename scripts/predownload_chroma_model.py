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
import ssl
import sys
import tarfile
import urllib.request
from pathlib import Path

socket.setdefaulttimeout(600)  # 10-minute socket timeout

REQUIRED_ONNX_FILES = (
    "config.json",
    "model.onnx",
    "special_tokens_map.json",
    "tokenizer_config.json",
    "tokenizer.json",
    "vocab.txt",
)

if len(sys.argv) > 1:
    model_dir = sys.argv[1]
else:
    model_dir = str(Path(__file__).parent.parent / "docker" / "chroma_model")

try:
    import chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 as _m

    model_path = Path(model_dir)
    extracted_path = model_path / "onnx"
    model_path.mkdir(parents=True, exist_ok=True)

    missing = [name for name in REQUIRED_ONNX_FILES if not (extracted_path / name).is_file()]
    if not missing:
        print("ChromaDB ONNX model already present, skipping download.", flush=True)
        raise SystemExit(0)

    src = inspect.getsource(_m)
    url = re.search(r"https://[^\s\"']+onnx\.tar\.gz", src).group()

    tar_path = model_path / "onnx.tar.gz"
    if tar_path.exists():
        tar_path.unlink()

    print(f"Downloading ChromaDB ONNX model from {url} ...", flush=True)
    import certifi

    ssl_context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(url, timeout=600, context=ssl_context) as response:
        tar_path.write_bytes(response.read())

    with tarfile.open(tar_path) as t:
        if sys.version_info >= (3, 12):
            t.extractall(model_path, filter="data")
        else:
            t.extractall(model_path)
    tar_path.unlink()

    print("ChromaDB ONNX model ready.", flush=True)

except SystemExit:
    raise
except Exception as e:
    print(f"Warning: model pre-download skipped ({e})", flush=True)
