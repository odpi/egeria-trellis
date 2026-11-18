"""Sentence-transformer embeddings on the shared device choice (packages/trellis-accel)."""
from __future__ import annotations

import logging

from sentence_transformers import SentenceTransformer

from resource_explorer.config import get_config

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None


def _resolve_device(device: str) -> str:
    """The shared device choice (packages/trellis-accel): this setting when it is not "auto", else TRELLIS_DEVICE,
    else an NVIDIA or AMD GPU, then Apple's GPU, then the CPU. A device this PyTorch cannot use raises
    trellis_accel.DeviceUnavailable with a sentence, instead of failing later inside torch."""
    from trellis_accel import pick_device

    choice = pick_device(device, purpose="embeddings")
    logger.info(f"embeddings: device {choice.device} ({choice.reason})")
    return choice.device


def get_embedding_model() -> SentenceTransformer:
    global _model
    if _model is None:
        cfg = get_config().embeddings
        device = _resolve_device(cfg.device)
        try:
            # Prefer cached model to avoid HF Hub network calls on every load
            _model = SentenceTransformer(cfg.model, device=device, local_files_only=True)
        except Exception:
            _model = SentenceTransformer(cfg.model, device=device)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    model = get_embedding_model()
    return model.encode(texts, convert_to_numpy=True).tolist()


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Alias for embed() used by MultiCollectionStore."""
    return embed(texts)
