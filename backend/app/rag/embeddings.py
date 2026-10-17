"""Local text embeddings behind LangChain's Embeddings interface, so the model is swappable."""

from functools import lru_cache

from fastembed import TextEmbedding
from langchain_core.embeddings import Embeddings

from app.core.config import get_settings


class FastEmbedEmbeddings(Embeddings):
    """fastembed (ONNX, CPU) model. Async calls run in a worker thread via the base class."""

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        self._model = TextEmbedding(model_name, cache_dir=cache_dir)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.passage_embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        vector = next(iter(self._model.query_embed(text)))
        return list(vector.tolist())


@lru_cache
def get_embeddings() -> Embeddings:
    """The configured model, loaded once per process."""
    settings = get_settings()
    return FastEmbedEmbeddings(settings.embedding_model, settings.embedding_cache_dir)
