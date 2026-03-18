"""Three-tier memory architecture.

- Episodic (ChromaDB): what happened — decisions, analyses, outcomes. MiniLM embeddings.
- Semantic (ChromaDB): what the system learned — patterns and rules. Gemini Embedding when available.
- Working (JSON): current state — positions, watchlist, pending analyses.
"""

from core.memory.episodic import EpisodicMemory
from core.memory.semantic import SemanticMemory
from core.memory.working import WorkingMemory

__all__ = ["EpisodicMemory", "SemanticMemory", "WorkingMemory"]
