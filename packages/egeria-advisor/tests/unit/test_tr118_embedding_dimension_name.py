"""TR-118: EA asks a sentence-transformers model for its dimension by the current name.

sentence-transformers 5.x renamed `get_sentence_embedding_dimension` to `get_embedding_dimension`; the old name is a
deprecated alias that logs a FutureWarning at every EA start (16 over 8 startups on laz) and will be removed. The
dependency floor is still `>=2.2.0`, so `embedding_dimension()` takes the new name where the model has it and the old
one only on a model that has nothing else.
"""
from __future__ import annotations

import re
import warnings
from pathlib import Path

import pytest

from advisor.embeddings import embedding_dimension

PKG = Path(__file__).resolve().parents[2]


class _Current:
    """5.x: the new name, and the old one as a deprecated alias (as SentenceTransformer has it)."""
    def get_embedding_dimension(self):
        return 384

    def get_sentence_embedding_dimension(self):
        warnings.warn("The `get_sentence_embedding_dimension` method has been renamed to `get_embedding_dimension`.",
                      FutureWarning, stacklevel=2)
        return self.get_embedding_dimension()


class _Older:
    """Before the rename: only the old name."""
    def get_sentence_embedding_dimension(self):
        return 768


def test_the_current_name_is_used_and_nothing_warns():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert embedding_dimension(_Current()) == 384


def test_an_older_model_with_only_the_old_name_still_answers():
    assert embedding_dimension(_Older()) == 768


def test_a_model_with_neither_name_says_so():
    with pytest.raises(AttributeError, match="embedding dimension"):
        embedding_dimension(object())


@pytest.mark.parametrize("path", ["advisor/embeddings.py", "scripts/convert_to_onnx.py", "scripts/test_end_to_end.py"])
def test_no_caller_asks_for_the_old_name_directly(path):
    text = (PKG / path).read_text()
    calls = re.findall(r"\.get_sentence_embedding_dimension\(\)", text)
    assert calls == [], f"{path}: {len(calls)} direct call(s) of the deprecated name; use embedding_dimension()"
