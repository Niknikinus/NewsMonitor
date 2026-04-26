import logging
import numpy as np
from typing import List, Dict, Tuple, Optional

from services.embeddings import cosine_similarity_matrix, get_embedding
from services.ai_processor import ask_grok
from config import settings

logger = logging.getLogger(__name__)


# ─── Cluster articles into stories ────────────────────────────────────────────

async def cluster_articles(articles: List[Dict]) -> List[List[Dict]]:
    """
    Group articles into story clusters using hierarchical agglomerative clustering
    on their embeddings.
    Returns a list of groups (each group = list of article dicts).
    """
    if len(articles) <= 1:
        return [[a] for a in articles]

    embeddings = [a.get("embedding") for a in articles]

    # Fall back to single-article groups if no embeddings
    if all(e is None for e in embeddings):
        logger.info("No embeddings — skipping clustering, each article is its own story")
        return [[a] for a in articles]

    # Fill missing embeddings with zeros
    dim = next((len(e) for e in embeddings if e is not None), 3072)
    filled = [e if e is not None else [0.0] * dim for e in embeddings]

    sim_matrix = cosine_similarity_matrix(filled)

    # Simple greedy clustering without sklearn
    threshold = settings.cluster_cosine_threshold
    labels = [-1] * n
    next_label = 0

    for i in range(n):
        if labels[i] == -1:
            labels[i] = next_label
            for j in range(i + 1, n):
                if labels[j] == -1 and sim_matrix[i][j] >= threshold:
                    labels[j] = next_label
            next_label += 1

    # Group articles by label
    groups: Dict[int, List[Dict]] = {}
    for idx, label in enumerate(labels):
        groups.setdefault(label, []).append(articles[idx])

    result = list(groups.values())
    logger.info(f"Clustering: {len(articles)} articles → {len(result)} clusters")
    return result


# ─── Generate cluster metadata with Grok-3 ───────────────────────────────────

async def generate_cluster_metadata(articles: List[Dict]) -> Dict:
    """
    Ask Grok-3 to generate a cluster title, summary, why-it-matters,
    and key angles for a group of articles.
    """
    if not settings.grok_api_key:
        # Fallback: use first article's title
        return {
            "title": articles[0].get("title", "Untitled Story"),
            "summary": "",
            "why_it_matters": "",
            "key_angles": [a.get("title", "") for a in articles[:3]],
        }

    # Build article list for prompt
    article_snippets = "\n".join(
        f"- {a.get('title', '')} ({a.get('source_name', '')}): {a.get('body', '')[:200]}"
        for a in articles[:10]
    )

    prompt = f"""You are a news editor. Analyze these articles and return JSON with:
- "title": A concise, informative cluster headline (max 12 words)
- "summary": 2-3 sentence summary of the overall story
- "why_it_matters": 1-2 sentences on significance
- "key_angles": array of 2-5 short angle labels (e.g. "About the merger", "About regulatory concerns")

Articles:
{article_snippets}

Respond with ONLY valid JSON, no markdown, no preamble."""

    response = await ask_grok(prompt, max_tokens=500)

    try:
        import json
        # Strip any markdown fences
        clean = response.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(clean)
        return {
            "title": data.get("title", articles[0].get("title", "Untitled")),
            "summary": data.get("summary", ""),
            "why_it_matters": data.get("why_it_matters", ""),
            "key_angles": data.get("key_angles", []),
        }
    except Exception as e:
        logger.warning(f"Failed to parse cluster metadata JSON: {e}\nResponse: {response[:200]}")
        return {
            "title": articles[0].get("title", "Untitled Story"),
            "summary": "",
            "why_it_matters": "",
            "key_angles": [a.get("title", "")[:60] for a in articles[:3]],
        }


# ─── Assign key angle per article ─────────────────────────────────────────────

async def assign_article_key_angles(
    articles: List[Dict],
    cluster_angles: List[str],
) -> None:
    """Assign a key_angle label to each article within its cluster."""
    if not cluster_angles:
        return

    for art in articles:
        if len(cluster_angles) == 1:
            art["key_angle"] = cluster_angles[0]
            continue

        title = art.get("title", "")
        options = "\n".join(f"{i+1}. {a}" for i, a in enumerate(cluster_angles))

        prompt = (
            f"Which angle best describes this article?\n"
            f"Article: {title}\n\nAngles:\n{options}\n\n"
            f"Reply with only the number."
        )

        resp = await ask_grok(prompt, max_tokens=5)
        try:
            idx = int(resp.strip()) - 1
            art["key_angle"] = cluster_angles[max(0, min(idx, len(cluster_angles) - 1))]
        except Exception:
            art["key_angle"] = cluster_angles[0]
