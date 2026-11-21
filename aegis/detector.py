"""Model inference API for the 4thwall multi-label HTTP WAF."""
from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter_ns
from typing import Any, Protocol
from urllib.parse import urlsplit

import torch
import transformers.modeling_utils as modeling_utils
from transformers import AutoModelForSequenceClassification, AutoTokenizer


@contextmanager
def _pytorch_safetensors_metadata():
    """Supply the missing PyTorch format tag expected by Transformers."""
    safe_open = modeling_utils.safe_open

    class MetadataFallback:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def metadata(self):
            return self.handle.metadata() or {"format": "pt"}

    modeling_utils.safe_open = lambda *args, **kwargs: MetadataFallback(safe_open(*args, **kwargs))
    try:
        yield
    finally:
        modeling_utils.safe_open = safe_open


class Detector(Protocol):
    def predict(self, request: Any) -> dict:
        """Return label, score, and inference latency for a mitmproxy request."""
        ...


DEFAULT_MODEL_DIR = "/opt/aegis/models/4thwall"

BLOCK_LABELS = {
    "242 - Code Injection", "88 - OS Command Injection", "126 - Path Traversal",
    "66 - SQL Injection", "16 - Dictionary-based Password Attack",
    "153 - Input Data Manipulation", "274 - HTTP Verb Tampering",
    "194 - Fake the Source of Data", "34 - HTTP Response Splitting",
    "33 - HTTP Request Smuggling",
}

DEFAULT_BLOCK_LABELS = BLOCK_LABELS


class ModelDetector:
    """4thwall multi-label detector with calibrated per-label thresholds."""

    def __init__(self, config: dict) -> None:
        model_dir = config.get("model", {}).get("directory", DEFAULT_MODEL_DIR)
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_dir, local_files_only=True, trust_remote_code=True,
        )
        with _pytorch_safetensors_metadata():
            self.model = AutoModelForSequenceClassification.from_pretrained(
                model_dir, local_files_only=True, trust_remote_code=True,
            ).cpu().eval()
        self.block_labels = set(config.get("block_categories", DEFAULT_BLOCK_LABELS))
        self.thresholds = self.model.config.thresholds
        self.labels = self.model.config.id2label

    def predict(self, request: Any) -> dict:
        started = perf_counter_ns()
        parsed = urlsplit(request.url)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        body = (request.raw_content or b"").decode("utf-8", "replace")
        serialized = self.model.serialize_request(
            method=request.method, path=path,
            ua=str(request.headers.get("user-agent", "")),
            ct=str(request.headers.get("content-type", "")), body=body,
        )
        inputs = self.tokenizer(serialized, return_tensors="pt", truncation=True, max_length=512)
        with torch.inference_mode():
            probabilities = torch.sigmoid(self.model(**inputs).logits[0]).tolist()
        detections = [
            {"label": self.labels[index], "score": float(probability)}
            for index, probability in enumerate(probabilities)
            if index and probability >= float(self.thresholds[self.labels[index]])
        ]
        primary = max(detections, key=lambda item: item["score"], default=None)
        return {
            "label": primary["label"] if primary else self.labels[0],
            "score": primary["score"] if primary else float(probabilities[0]),
            "detections": detections,
            "blocked": any(item["label"] in self.block_labels for item in detections),
            "latency_ms": (perf_counter_ns() - started) / 1_000_000,
        }
