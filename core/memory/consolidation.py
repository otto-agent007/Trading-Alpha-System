from __future__ import annotations

import json
import logging
from datetime import datetime

from config import OBSIDIAN_VAULT
from core.memory.episodic import EpisodicMemory
from core.memory.semantic import SemanticMemory
from core.router import ModelRouter

logger = logging.getLogger(__name__)


def consolidate(
    episodic: EpisodicMemory,
    semantic: SemanticMemory,
    router: ModelRouter,
) -> None:
    """Nightly consolidation: extract learnings from recent episodes.

    1. Get episodes from last 24h
    2. Identify resolved markets with known outcomes
    3. Ask heavy LLM to extract patterns
    4. Store new learnings / update existing confidence
    5. Prune stale low-confidence patterns
    6. Write summary to Obsidian vault
    """
    logger.info("Consolidation: starting nightly review...")

    recent = episodic.get_recent(hours=24)
    if not recent:
        logger.info("Consolidation: no recent episodes, skipping")
        return

    resolved = [ep for ep in recent if ep.get("outcome")]
    all_count = len(recent)
    resolved_count = len(resolved)

    logger.info(f"Consolidation: {all_count} recent episodes, {resolved_count} with outcomes")

    if resolved:
        _extract_learnings(resolved, semantic, router)

    pruned = semantic.prune(min_confidence=0.3, min_evidence=10)
    if pruned:
        logger.info(f"Consolidation: pruned {pruned} low-confidence patterns")

    _write_summary(recent, semantic)

    logger.info(f"Consolidation: complete. {router.get_usage_summary()}")


def _extract_learnings(
    resolved: list[dict],
    semantic: SemanticMemory,
    router: ModelRouter,
) -> None:
    """Ask the heavy LLM to identify patterns from resolved episodes."""
    trimmed = []
    for ep in resolved[:15]:
        trimmed.append({
            "market_id": ep.get("market_id", ""),
            "category": ep.get("category", ""),
            "question": ep.get("question", "")[:100],
            "action": ep.get("action", ""),
            "outcome": ep.get("outcome", ""),
            "edge": ep.get("edge", ""),
            "confidence": ep.get("confidence", ""),
        })

    prompt = (
        f"Review these {len(trimmed)} prediction market outcomes:\n"
        f"{json.dumps(trimmed, indent=2)}\n\n"
        "Extract 1-3 actionable patterns you observe. For each pattern, provide:\n"
        "- category: what type of market this applies to\n"
        "- pattern: a specific, testable observation (one sentence)\n"
        "- confidence: 0.0-1.0 based on evidence strength\n"
        "- evidence_count: how many of the above episodes support this\n\n"
        'Return JSON: {"patterns": [...]}'
    )

    try:
        raw = router.reason(
            prompt,
            system="You are a quantitative prediction market researcher. Be specific and evidence-based.",
            temperature=0.3,
        )
        data = json.loads(raw)
        patterns = data.get("patterns", [])

        for pattern in patterns:
            existing = semantic.query_patterns(
                f"{pattern.get('category', '')}: {pattern.get('pattern', '')}",
                n=1,
            )

            if existing and _is_similar(existing[0], pattern):
                # FIX: query_patterns now returns _id from ChromaDB
                learning_id = existing[0].get("_id", "")
                if learning_id:
                    semantic.update_confidence(learning_id, correct=True)
                    logger.info(
                        f"Consolidation: reinforced existing pattern: "
                        f"{existing[0].get('pattern', '')[:60]}"
                    )
                else:
                    logger.warning("Consolidation: matched pattern has no _id, storing as new")
                    semantic.store_learning(pattern)
            else:
                semantic.store_learning(pattern)
                logger.info(f"Consolidation: new pattern: {pattern.get('pattern', '')[:60]}")

    except Exception as e:
        logger.error(f"Consolidation: learning extraction failed: {e}")


def _is_similar(existing: dict, new: dict) -> bool:
    """Check if two patterns are about the same thing (same category + word overlap)."""
    if existing.get("category", "").lower() != new.get("category", "").lower():
        return False
    existing_words = set(existing.get("pattern", "").lower().split())
    new_words = set(new.get("pattern", "").lower().split())
    if not existing_words or not new_words:
        return False
    overlap = len(existing_words & new_words) / len(existing_words | new_words)
    return overlap > 0.5


def _write_summary(recent: list[dict], semantic: SemanticMemory) -> None:
    """Write a daily consolidation summary to the Obsidian vault."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    all_patterns = semantic.get_all()

    lines = [
        "---",
        f"date: {date_str}",
        "type: review",
        "tags: [consolidation, nightly]",
        "---",
        "",
        f"# Nightly Consolidation — {date_str}",
        "",
        f"**Episodes reviewed:** {len(recent)}  ",
        f"**Total learned patterns:** {len(all_patterns)}  ",
        "",
        "## Top Patterns (by confidence)",
        "",
        "| Category | Pattern | Confidence | Evidence |",
        "|----------|---------|------------|----------|",
    ]

    sorted_patterns = sorted(
        all_patterns, key=lambda p: float(p.get("confidence", 0)), reverse=True
    )
    for p in sorted_patterns[:10]:
        cat = p.get("category", "?")
        pat = p.get("pattern", "?")[:80]
        conf = f"{float(p.get('confidence', 0)):.2f}"
        ev = p.get("evidence_count", 0)
        lines.append(f"| {cat} | {pat} | {conf} | {ev} |")

    lines += [
        "",
        "## Recent Episodes",
        "",
        "```dataview",
        "TABLE market_id, platform, action, outcome, date",
        'FROM "Alpha Research"',
        'WHERE type = "decision"',
        "SORT date DESC",
        "LIMIT 10",
        "```",
    ]

    vault_path = OBSIDIAN_VAULT / "Alpha Research" / "Dashboard" / f"Consolidation_{date_str}.md"
    vault_path.parent.mkdir(parents=True, exist_ok=True)
    vault_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Consolidation summary written: {vault_path}")
