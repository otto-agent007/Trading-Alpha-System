import json
import logging
import pandas as pd
from datetime import datetime
from pathlib import Path
from config import OBSIDIAN_VAULT
from core.hybrid_memory import memory

logger = logging.getLogger(__name__)


class ObsidianOrchestrator:
    @staticmethod
    def write_dashboard(
        hyp,                            # MarketHypothesis
        metrics,                        # MarketEdgeMetrics
        verdict,                        # MarketVerdict
        open_df: pd.DataFrame | None = None,
    ) -> None:
        date_str = datetime.now().strftime("%Y%m%d")
        hyp_id = verdict.hypothesis_id
        dashboard_dir = Path(OBSIDIAN_VAULT) / "Alpha Research" / "Dashboard"
        dashboard_dir.mkdir(parents=True, exist_ok=True)

        # --- Find currently open markets that match the hypothesis ---
        matching_open: list[dict] = []
        if open_df is not None and not open_df.empty:
            lo, hi = hyp.prob_range
            filtered = open_df[
                (open_df["category"].str.lower() == hyp.category.lower())
                & (open_df["yes_prob"].between(lo, hi))
                & (open_df["volume_usd"] >= hyp.min_volume_usd)
            ].head(5)
            matching_open = filtered[
                ["title", "yes_prob", "days_to_close", "volume_usd"]
            ].to_dict("records")

        matches_path = dashboard_dir / f"matches_{hyp_id}.json"
        matches_path.write_text(json.dumps(matching_open, default=str, indent=2))

        # --- Markdown dashboard ---
        status_label = "ACCEPTED" if verdict.accepted else "REJECTED"
        prob_lo, prob_hi = hyp.prob_range
        days_min, days_max = hyp.days_to_resolution

        md_lines = [
            "---",
            f"date: {date_str}",
            f"hypothesis_id: {hyp_id}",
            f"category: {hyp.category}",
            f"accepted: {str(verdict.accepted).lower()}",
            "---",
            "",
            f"# Alpha Cycle {hyp_id}",
            "",
            f"**Verdict**: {status_label}  ",
            f"**Category**: {hyp.category} | **Position**: {hyp.position}  ",
            f"**Entry prob range**: {prob_lo:.0%} \u2013 {prob_hi:.0%}  ",
            f"**Market lifetime**: {days_min}\u2013{days_max} days  ",
            f"**Min volume**: ${hyp.min_volume_usd:,.0f}  ",
            "",
            "## Edge Metrics",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Expected Value | {metrics.expected_value:.4f} |",
            f"| Win Rate | {metrics.win_rate:.2%} |",
            f"| Sample Size | {metrics.sample_size} markets |",
            f"| Avg Entry Prob | {metrics.avg_entry_prob:.3f} |",
            f"| Kelly Fraction | {verdict.kelly_fraction:.3f} |",
            "",
            f"**Reason**: {verdict.reason}",
            "",
            "## Rationale",
            "",
            hyp.rationale,
            "",
            "## Matching Open Markets",
            "",
        ]

        if matching_open:
            md_lines += [
                "| Market | YES Prob | Days Left | Volume |",
                "|--------|----------|-----------|--------|",
            ]
            for m in matching_open:
                yes_str = f"{m['yes_prob']:.2%}" if m.get("yes_prob") is not None else "N/A"
                days = m.get("days_to_close", "?")
                vol = f"${m['volume_usd']:,.0f}"
                title = str(m["title"])[:60]
                md_lines.append(f"| {title} | {yes_str} | {days} | {vol} |")
        else:
            md_lines.append("_No matching open markets at this time._")

        md_lines += [
            "",
            "```dataview",
            "TABLE hypothesis_id, category, accepted, date",
            'FROM "Alpha Research/Dashboard"',
            "WHERE hypothesis_id != null",
            "SORT date DESC",
            "```",
        ]

        md_path = dashboard_dir / f"Cycle_{hyp_id}.md"
        md_path.write_text("\n".join(md_lines))

        # --- Quarto report ---
        qmd_lines = [
            "---",
            f'title: "Alpha Report {hyp_id}"',
            "format:",
            "  html:",
            "    toc: true",
            "    code-fold: true",
            "---",
            "",
            f"## Hypothesis: {hyp.category.capitalize()} {hyp.position} @ {prob_lo:.0%}\u2013{prob_hi:.0%}",
            "",
            f"**Verdict**: {status_label} | **EV**: {metrics.expected_value:.4f} | **Kelly**: {verdict.kelly_fraction:.3f}",
            "",
            f"> {hyp.rationale}",
            "",
            "```{python}",
            "import json, pandas as pd, plotly.express as px",
            f'matches = json.load(open("matches_{hyp_id}.json"))',
            "if matches:",
            "    df = pd.DataFrame(matches)",
            '    fig = px.scatter(df, x="yes_prob", y="volume_usd", text="title",',
            f'        title="Open Markets Matching Hypothesis ({hyp.category})",',
            '        labels={"yes_prob": "YES Probability", "volume_usd": "Volume (USD)"})',
            "    fig.show()",
            "else:",
            '    print("No matching open markets found.")',
            "```",
        ]
        qmd_path = dashboard_dir / f"Report_{hyp_id}.qmd"
        qmd_path.write_text("\n".join(qmd_lines))

        # --- Store episode in ChromaDB memory ---
        episode = {
            "id": hyp_id,
            "category": hyp.category,
            "prob_range_lo": hyp.prob_range[0],
            "prob_range_hi": hyp.prob_range[1],
            "days_min": hyp.days_to_resolution[0],
            "days_max": hyp.days_to_resolution[1],
            "min_volume_usd": hyp.min_volume_usd,
            "position": hyp.position,
            "rationale": hyp.rationale,
            "win_rate": metrics.win_rate,
            "expected_value": metrics.expected_value,
            "sample_size": metrics.sample_size,
            "accepted": verdict.accepted,
            "kelly_fraction": verdict.kelly_fraction,
        }
        memory.add_episode(episode)

        logger.info(f"Dashboard written: {md_path}")
