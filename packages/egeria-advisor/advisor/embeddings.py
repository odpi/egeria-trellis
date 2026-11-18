"""
Embedding generation for Egeria Advisor.

This module provides GPU-accelerated embedding generation using sentence-transformers.
Supports batch processing and caching for efficient vector generation.
"""

from dotenv import load_dotenv

# Must run before `sentence_transformers` (and the `huggingface_hub` it pulls in)
# is imported below — huggingface_hub reads HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE/
# HF_HUB_DOWNLOAD_TIMEOUT from os.environ once, at import time, so .env values
# have to already be real process env vars by then.
load_dotenv()

import os as _os
import sys as _sys
from pathlib import Path as _Path


def hf_hub_cache_dir() -> _Path:
    """The Hugging Face hub cache, by huggingface_hub's own precedence.

    HF_HUB_CACHE, else HUGGINGFACE_HUB_CACHE (the older name), else
    $HF_HOME/hub, else ~/.cache/huggingface/hub. Computed here without
    importing huggingface_hub, because importing it fixes its offline flag.
    """
    for var in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        val = _os.environ.get(var, "").strip()
        if val:
            return _Path(val).expanduser()
    hf_home = _os.environ.get("HF_HOME", "").strip()
    if hf_home:
        return _Path(hf_home).expanduser() / "hub"
    xdg = _os.environ.get("XDG_CACHE_HOME", "").strip()
    base = _Path(xdg).expanduser() if xdg else _Path.home() / ".cache"
    return base / "huggingface" / "hub"


def hf_model_cached(model_name: str, cache_dir: "_Path | None" = None) -> bool:
    """True when ``model_name`` can load with no network.

    A local directory counts. A hub id ("org/name", or a bare sentence-
    transformers name, which resolves to "sentence-transformers/<name>") counts
    when its cache entry has at least one non-empty snapshot.
    """
    if not model_name:
        return False
    if _Path(model_name).expanduser().is_dir():
        return True
    repo_id = model_name if "/" in model_name else f"sentence-transformers/{model_name}"
    entry = (cache_dir or hf_hub_cache_dir()) / ("models--" + repo_id.replace("/", "--"))
    snapshots = entry / "snapshots"
    try:
        return any(snap.is_dir() and any(snap.iterdir()) for snap in snapshots.iterdir())
    except OSError:
        return False


def _configured_model_name() -> str:
    try:
        from advisor.config import get_full_config
        return get_full_config()["embeddings"].model
    except Exception:  # pragma: no cover - config problems surface at EmbeddingGenerator()
        return ""


def prefer_offline_if_cached(model_name: str, cache_dir: "_Path | None" = None) -> str:
    """Set HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE (setdefault) when the model is cached.

    Returns how the model will load: "offline" (cached; the flags are set or
    were already 1), "explicit-online" (cached, but the operator set a flag to
    something other than 1, which is left alone) or "download" (not cached: the
    first load fetches it from the Hugging Face Hub).

    Must run before huggingface_hub is imported: it reads these variables once,
    at import. If it was already imported (by another module), its constant is
    updated too, so the flag still takes effect.
    """
    if not hf_model_cached(model_name, cache_dir):
        return "download"
    _os.environ.setdefault("HF_HUB_OFFLINE", "1")
    _os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    if _os.environ["HF_HUB_OFFLINE"].strip().lower() not in ("1", "true", "yes", "on"):
        return "explicit-online"
    hub_constants = _sys.modules.get("huggingface_hub.constants")
    if hub_constants is not None and hasattr(hub_constants, "HF_HUB_OFFLINE"):
        hub_constants.HF_HUB_OFFLINE = True
    return "offline"


#: How the configured model loads; decided before sentence_transformers is imported.
_MODEL_LOAD_MODE = {"model": _configured_model_name()}
_MODEL_LOAD_MODE["mode"] = prefer_offline_if_cached(_MODEL_LOAD_MODE["model"])

import torch
from sentence_transformers import SentenceTransformer
from typing import List, Union, Optional
from loguru import logger
import numpy as np
from pathlib import Path
import json

from advisor.config import settings, get_full_config, ensure_writable_dir


class EmbeddingGenerator:
    """Generate embeddings using sentence-transformers with GPU acceleration."""

    def __init__(
        self,
        model_name: Optional[str] = None,
        device: Optional[str] = None,
        batch_size: Optional[int] = None,
        cache_dir: Optional[Path] = None
    ):
        """
        Initialize the embedding generator.

        Args:
            model_name: Name of the sentence-transformers model
            device: Device to use ('cuda', 'cpu', or 'auto')
            batch_size: Batch size for encoding
            cache_dir: Directory for caching embeddings
        """
        # Get embedding config
        full_config = get_full_config()
        embedding_config = full_config.get("embeddings")

        self.model_name = model_name or embedding_config.model
        self.batch_size = batch_size or embedding_config.batch_size
        self.cache_dir = cache_dir or Path(settings.advisor_cache_dir) / "embeddings"
        ensure_writable_dir(self.cache_dir, "ADVISOR_CACHE_DIR")

        # Determine device with universal GPU detection
        if device is None:
            device = embedding_config.device

        if device == "auto":
            self.device = self._detect_best_device()
        else:
            self.device = device

        logger.info(f"Initializing embedding model: {self.model_name}")
        logger.info(f"Using device: {self.device}")
        self.load_mode = self._announce_load_mode()

        # Load model
        try:
            # Force CPU mode if specified to avoid GPU initialization issues
            if self.device == "cpu":
                import os
                os.environ["CUDA_VISIBLE_DEVICES"] = ""
            
            self.model = SentenceTransformer(self.model_name, device=self.device)
            self.embedding_dim = self.model.get_sentence_embedding_dimension()
            logger.info(f"Model loaded successfully. Embedding dimension: {self.embedding_dim}")
            self._log_loaded()

            # Test GPU if using any GPU device
            if self.device in ["cuda", "mps"] or "hip" in self.device:
                self._test_gpu()

        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            # If GPU fails, try falling back to CPU
            if self.device != "cpu":
                logger.warning(f"{self.device.upper()} initialization failed, falling back to CPU")
                self.device = "cpu"
                import os
                os.environ["CUDA_VISIBLE_DEVICES"] = ""
                try:
                    self.model = SentenceTransformer(self.model_name, device="cpu")
                    self.embedding_dim = self.model.get_sentence_embedding_dimension()
                    logger.info(f"Model loaded successfully on CPU. Embedding dimension: {self.embedding_dim}")
                    self._log_loaded()
                except Exception as cpu_error:
                    logger.error(f"Failed to load embedding model on CPU: {cpu_error}")
                    raise
            else:
                raise

    def _announce_load_mode(self) -> str:
        """Say once whether the model comes from the local cache or the network."""
        if self.model_name == _MODEL_LOAD_MODE["model"]:
            mode = _MODEL_LOAD_MODE["mode"]
        else:
            mode = prefer_offline_if_cached(self.model_name)
        if mode == "offline":
            pass  # said after the load succeeds (_log_loaded), so it is never claimed for a failed load
        elif mode == "explicit-online":
            logger.info(f"embedding model {self.model_name} is cached; HF_HUB_OFFLINE is set to "
                        f"{_os.environ.get('HF_HUB_OFFLINE')!r}, so the Hub may still be checked")
        else:
            logger.warning(
                f"embedding model {self.model_name} is not in the Hugging Face cache "
                f"({hf_hub_cache_dir()}): downloading it from huggingface.co. Later starts "
                f"load it offline from the cache."
            )
        return mode

    def _log_loaded(self) -> None:
        if self.load_mode == "offline":
            logger.info(f"embedding model loaded from cache (offline): {self.model_name}")

    def _detect_best_device(self) -> str:
        """The device for an embeddings device of "auto": the shared choice
        (packages/trellis-accel): TRELLIS_DEVICE when set, else an NVIDIA or AMD (ROCm) GPU,
        then Apple's GPU, then the CPU, and says why. EA's setting is advisor.yaml's
        `embeddings.device`, shipped as "auto" (EmbeddingConfig's own default, "cpu", applies
        only when the file leaves it out); EMBEDDING_DEVICE is not read on this path."""
        from trellis_accel import pick_device

        choice = pick_device("auto", purpose="embeddings")
        logger.info(f"Embedding device: {choice.device} ({choice.reason})")
        return choice.device

    def _test_gpu(self):
        """Test GPU functionality with a sample encoding."""
        device_type = "GPU" if self.device in ["cuda", "mps"] or "hip" in self.device else self.device.upper()
        if self.device in ["cuda", "mps"] or "hip" in self.device:
            try:
                # Test encoding with the actual device being used
                self.model.encode("test", device=self.device)
                logger.info(f"✓ {device_type} encoding verified successfully")
            except Exception as e:
                logger.warning(f"{device_type} test failed ({e}), falling back to CPU")
                self.device = "cpu"
                self.model = SentenceTransformer(self.model_name, device="cpu")

    def generate_embedding(self, text: str) -> np.ndarray:
        """
        Generate embedding for a single text.

        Args:
            text: Text to encode

        Returns:
            Embedding as numpy array
        """
        return self.encode(text, convert_to_numpy=True)[0]

    def encode(
        self,
        texts: Union[str, List[str]],
        normalize: bool = True,
        show_progress: bool = False,
        convert_to_numpy: bool = True
    ) -> Union[np.ndarray, torch.Tensor]:
        """
        Encode text(s) into embeddings.

        Args:
            texts: Single text or list of texts to encode
            normalize: Whether to normalize embeddings to unit length
            show_progress: Show progress bar for batch encoding
            convert_to_numpy: Convert output to numpy array

        Returns:
            Embeddings as numpy array or torch tensor
        """
        if isinstance(texts, str):
            texts = [texts]

        logger.debug(f"Encoding {len(texts)} texts")

        try:
            embeddings = self.model.encode(
                texts,
                batch_size=self.batch_size,
                show_progress_bar=show_progress,
                normalize_embeddings=normalize,
                convert_to_numpy=convert_to_numpy,
                device=self.device
            )

            logger.debug(f"Generated embeddings shape: {embeddings.shape}")
            return embeddings

        except Exception as e:
            if self.device == "cuda":
                logger.warning(f"Encoding failed on GPU ({e}), retrying on CPU...")
                # Switch to CPU for this and future calls
                self.device = "cpu"
                self.model.to("cpu")
                return self.model.encode(
                    texts,
                    batch_size=self.batch_size,
                    show_progress_bar=show_progress,
                    normalize_embeddings=normalize,
                    convert_to_numpy=convert_to_numpy,
                    device="cpu"
                )
            else:
                logger.error(f"Failed to encode texts: {e}")
                raise

    def encode_batch(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        show_progress: bool = True
    ) -> np.ndarray:
        """
        Encode a large batch of texts efficiently.

        Args:
            texts: List of texts to encode
            batch_size: Override default batch size
            show_progress: Show progress bar

        Returns:
            Numpy array of embeddings
        """
        batch_size = batch_size or self.batch_size

        logger.info(f"Encoding {len(texts)} texts in batches of {batch_size}")

        return self.encode(
            texts,
            normalize=True,
            show_progress=show_progress,
            convert_to_numpy=True
        )

    def encode_with_cache(
        self,
        texts: List[str],
        cache_key: str,
        force_refresh: bool = False
    ) -> np.ndarray:
        """
        Encode texts with caching support.

        Args:
            texts: List of texts to encode
            cache_key: Unique key for caching
            force_refresh: Force re-encoding even if cached

        Returns:
            Numpy array of embeddings
        """
        cache_file = self.cache_dir / f"{cache_key}.npy"
        metadata_file = self.cache_dir / f"{cache_key}_meta.json"

        # Check cache
        if not force_refresh and cache_file.exists():
            logger.info(f"Loading embeddings from cache: {cache_key}")
            try:
                embeddings = np.load(cache_file)

                # Verify cache metadata
                if metadata_file.exists():
                    with open(metadata_file) as f:
                        meta = json.load(f)
                        if meta.get("count") == len(texts) and meta.get("model") == self.model_name:
                            logger.info(f"✓ Cache valid, loaded {len(embeddings)} embeddings")
                            return embeddings

                logger.warning("Cache metadata mismatch, re-encoding")
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}")

        # Generate embeddings
        logger.info(f"Generating embeddings for cache key: {cache_key}")
        embeddings = self.encode_batch(texts, show_progress=True)

        # Save to cache
        try:
            np.save(cache_file, embeddings)

            metadata = {
                "count": len(texts),
                "model": self.model_name,
                "dimension": self.embedding_dim,
                "device": self.device
            }
            with open(metadata_file, "w") as f:
                json.dump(metadata, f, indent=2)

            logger.info(f"✓ Saved embeddings to cache: {cache_key}")
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")

        return embeddings

    def get_embedding_dim(self) -> int:
        """Get the embedding dimension."""
        return self.embedding_dim

    def get_model_info(self) -> dict:
        """Get information about the loaded model."""
        return {
            "model_name": self.model_name,
            "embedding_dim": self.embedding_dim,
            "device": self.device,
            "batch_size": self.batch_size,
            "max_seq_length": self.model.max_seq_length
        }


# Global instance for reuse
_embedding_generator: Optional[Union[EmbeddingGenerator, 'ONNXEmbeddingGenerator']] = None


def get_embedding_generator() -> Union[EmbeddingGenerator, 'ONNXEmbeddingGenerator']:
    """
    Get or create the global embedding generator instance.
    
    Automatically selects backend based on configuration:
    - backend='pytorch': Use PyTorch sentence-transformers (default)
    - backend='onnx': Use ONNX Runtime for 2-3x speedup
    
    Returns
    -------
    EmbeddingGenerator or ONNXEmbeddingGenerator
        Embedding generator instance
    """
    global _embedding_generator

    if _embedding_generator is None:
        # Get configuration
        full_config = get_full_config()
        embedding_config = full_config.get("embeddings")
        backend = getattr(embedding_config, "backend", "pytorch")
        
        if backend == "onnx":
            # Use ONNX backend
            try:
                from advisor.embeddings_onnx import ONNXEmbeddingGenerator
                
                onnx_config = getattr(embedding_config, "onnx", {})
                if isinstance(onnx_config, dict):
                    onnx_model_path = onnx_config.get("model_path", "models/all-MiniLM-L6-v2.optimized.onnx")
                    providers = onnx_config.get("providers")
                else:
                    onnx_model_path = getattr(onnx_config, "model_path", "models/all-MiniLM-L6-v2.optimized.onnx")
                    providers = getattr(onnx_config, "providers", None)
                
                logger.info(f"Using ONNX backend: {onnx_model_path}")
                _embedding_generator = ONNXEmbeddingGenerator(
                    onnx_model_path=onnx_model_path,
                    tokenizer_name=embedding_config.model,
                    batch_size=embedding_config.batch_size,
                    max_length=getattr(embedding_config, "max_length", 512),
                    providers=providers
                )
            except Exception as e:
                logger.warning(f"Failed to load ONNX backend: {e}")
                logger.info("Falling back to PyTorch backend")
                _embedding_generator = EmbeddingGenerator()
        else:
            # Use PyTorch backend (default)
            logger.info("Using PyTorch backend")
            _embedding_generator = EmbeddingGenerator()

    return _embedding_generator


def encode_text(text: Union[str, List[str]], **kwargs) -> np.ndarray:
    """
    Convenience function to encode text using the global generator.

    Args:
        text: Text or list of texts to encode
        **kwargs: Additional arguments passed to encode()

    Returns:
        Numpy array of embeddings
    """
    generator = get_embedding_generator()
    return generator.encode(text, **kwargs)
