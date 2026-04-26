import json
import logging
import numpy as np
from typing import List, Optional
import httpx

from config import settings

logger = logging.getLogger(__name__)


async def get_embedding(text: str) -> Optional[List[float]]:
    """Get a single embedding vector via OpenAI-compatible API (GitHub Marketplace)."""
    if not settings.openai_api_key:
        logger.warning("OpenAI API key not set — embedding skipped")
        return None

    text = text.replace("\n", " ")[:8000]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.openai_api_base}/embeddings",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.openai_embedding_model,
                    "input": text,
                    "encoding_format": "float",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]
    except Exception as e:
        logger.error(f"Embedding error: {e}")
        return None


async def get_embeddings_batch(texts: List[str]) -> List[Optional[List[float]]]:
    """Get embeddings for a batch of texts."""
    if not settings.openai_api_key:
        return [None] * len(texts)

    # OpenAI supports up to 2048 texts per batch; we chunk by 100
    results: List[Optional[List[float]]] = []
    chunk_size = 50

    for i in range(0, len(texts), chunk_size):
        chunk = [t.replace("\n", " ")[:8000] for t in texts[i:i + chunk_size]]
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{settings.openai_api_base}/embeddings",
                    headers={
                        "Authorization": f"Bearer {settings.openai_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.openai_embedding_model,
                        "input": chunk,
                        "encoding_format": "float",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                # data["data"] is sorted by index
                sorted_data = sorted(data["data"], key=lambda x: x["index"])
                results.extend([item["embedding"] for item in sorted_data])
        except Exception as e:
            logger.error(f"Batch embedding error (chunk {i}): {e}")
            results.extend([None] * len(chunk))

    return results


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    na = np.array(a, dtype=np.float32)
    nb = np.array(b, dtype=np.float32)
    norm_a = np.linalg.norm(na)
    norm_b = np.linalg.norm(nb)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(na, nb) / (norm_a * norm_b))


def cosine_similarity_matrix(
    embeddings: List[List[float]],
) -> np.ndarray:
    """Compute pairwise cosine similarity matrix."""
    mat = np.array(embeddings, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1e-9, norms)
    mat_norm = mat / norms
    return mat_norm @ mat_norm.T


def serialize_embedding(embedding: List[float]) -> str:
    return json.dumps(embedding)


def deserialize_embedding(data: str) -> List[float]:
    return json.loads(data)
