"""Generate benchmark chart PNGs for README.

Dark theme matching unity-api-mcp visual style:
- Background: #1A1F2E
- Cyan (#00D9FF) for MCP, Purple (#CC99FF) for Skilled, Coral (#FF6B6B) for Naive
- White text, clean layout, ~1400x700px output
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "images"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# -- Colors & Theme --
BG = "#1A1F2E"
CARD_BG = "#232A3D"
CYAN = "#00D9FF"
PURPLE = "#CC99FF"
CORAL = "#FF6B6B"
WHITE = "#FFFFFF"
GRAY = "#8892A4"
GREEN = "#66FF99"

plt.rcParams.update({
    "figure.facecolor": BG,
    "axes.facecolor": BG,
    "axes.edgecolor": GRAY,
    "axes.labelcolor": WHITE,
    "xtick.color": WHITE,
    "ytick.color": WHITE,
    "text.color": WHITE,
    "font.family": "sans-serif",
    "font.size": 13,
})


def _save(fig: plt.Figure, name: str) -> None:
    path = OUT_DIR / name
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  saved {path}")


# ============================================================
# Chart 1: Cross-Language Token Comparison (grouped horizontal bars)
# ============================================================
def chart_total_tokens() -> None:
    languages = ["Python", "Go", "C#", "TypeScript", "Java"]
    mcp =    [753, 1791, 1021, 1451, 1851]
    skilled = [2000, 2770, 4453, 4500, 4200]
    naive =  [10400, 15300, 11825, 14550, 26700]

    fig, ax = plt.subplots(figsize=(14, 7))
    y = np.arange(len(languages))
    h = 0.25

    bars_naive   = ax.barh(y + h, naive, h, color=CORAL, label="Naive (Grep + full Read)", zorder=3)
    bars_skilled = ax.barh(y, skilled, h, color=PURPLE, label="Skilled (Grep + partial Read)", zorder=3)
    bars_mcp     = ax.barh(y - h, mcp, h, color=CYAN, label="MCP (codesurface)", zorder=3)

    # Value labels
    for bars in [bars_mcp, bars_skilled, bars_naive]:
        for bar in bars:
            w = bar.get_width()
            ax.text(w + 200, bar.get_y() + bar.get_height() / 2,
                    f"{int(w):,}", va="center", fontsize=11, color=WHITE)

    ax.set_yticks(y)
    ax.set_yticklabels(languages, fontsize=14, fontweight="bold")
    ax.set_xlabel("Total Tokens (10-step workflow)", fontsize=13)
    ax.set_title("Total Tokens — 10-Step Research Workflow", fontsize=18,
                 fontweight="bold", pad=20)
    ax.legend(loc="center right", fontsize=12, framealpha=0.3,
              edgecolor=GRAY, facecolor=CARD_BG)
    ax.set_xlim(0, max(naive) * 1.18)
    ax.grid(axis="x", color=GRAY, alpha=0.2, zorder=0)
    ax.invert_yaxis()
    fig.tight_layout()
    _save(fig, "01-total-tokens.png")


# ============================================================
# Chart 2: Per-Question Token Breakdown (selected highlights)
# ============================================================
def chart_per_step() -> None:
    steps = [
        ("Go: Read request headers\n(context.go — 1,200 lines)", 40, 120, 9600),
        ("Java: Input validation\n(Preconditions — 80 overloads)", 175, 400, 6000),
        ("C#: Wire new camp logic\n(CampEntryPoint.cs)", 54, 571, 3803),
        ("Java: Cache size limits\n(CacheBuilder — 190-line Javadoc)", 175, 350, 5500),
        ("Go: Middleware structure\n(gin.go — 400 lines)", 150, 300, 3200),
        ("TS: Album controller API\n(13 decorator-heavy methods)", 300, 750, 2100),
    ]

    labels = [s[0] for s in steps]
    mcp =    [s[1] for s in steps]
    skilled = [s[2] for s in steps]
    naive =  [s[3] for s in steps]

    fig, ax = plt.subplots(figsize=(14, 7))
    y = np.arange(len(labels))
    h = 0.25

    bars_naive   = ax.barh(y + h, naive, h, color=CORAL, label="Naive", zorder=3)
    bars_skilled = ax.barh(y, skilled, h, color=PURPLE, label="Skilled", zorder=3)
    bars_mcp     = ax.barh(y - h, mcp, h, color=CYAN, label="MCP", zorder=3)

    for bars in [bars_mcp, bars_skilled, bars_naive]:
        for bar in bars:
            w = bar.get_width()
            ax.text(w + 80, bar.get_y() + bar.get_height() / 2,
                    f"{int(w):,}", va="center", fontsize=10, color=WHITE)

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=11)
    ax.set_xlabel("Tokens", fontsize=13)
    ax.set_title("Token Cost Per Question — Selected Highlights", fontsize=18,
                 fontweight="bold", pad=20)
    ax.legend(loc="lower right", fontsize=12, framealpha=0.3,
              edgecolor=GRAY, facecolor=CARD_BG)
    ax.set_xlim(0, max(naive) * 1.15)
    ax.grid(axis="x", color=GRAY, alpha=0.2, zorder=0)
    ax.invert_yaxis()
    fig.tight_layout()
    _save(fig, "02-per-step.png")


# ============================================================
# Chart 3: Realistic Hybrid Workflow
# ============================================================
def chart_hybrid() -> None:
    strategies = ["Naive Agent", "Skilled Agent", "MCP + Targeted Read"]
    values = [78775, 17923, 9999]
    colors = [CORAL, PURPLE, CYAN]

    fig, ax = plt.subplots(figsize=(14, 5))

    bars = ax.barh(strategies, values, color=colors, height=0.5, zorder=3)

    for bar, val in zip(bars, values):
        # Label inside bar for large values, outside for small
        x_pos = val - 800 if val > 15000 else val + 500
        ha = "right" if val > 15000 else "left"
        ax.text(x_pos, bar.get_y() + bar.get_height() / 2,
                f"{val:,} tokens", va="center", ha=ha, fontsize=14,
                fontweight="bold", color=WHITE)

    # Annotation arrows for savings
    ax.annotate("44% fewer", xy=(17923, 1), xytext=(35000, 1.3),
                fontsize=13, color=GREEN, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=GREEN, lw=1.5))
    ax.annotate("87% fewer", xy=(78775, 0), xytext=(55000, -0.3),
                fontsize=13, color=GREEN, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=GREEN, lw=1.5))

    ax.set_xlabel("Total Tokens (all 5 languages combined)", fontsize=13)
    ax.set_title("Realistic Workflow: MCP + Targeted Read", fontsize=18,
                 fontweight="bold", pad=20)
    ax.set_xlim(0, max(values) * 1.12)
    ax.grid(axis="x", color=GRAY, alpha=0.2, zorder=0)
    ax.invert_yaxis()
    fig.tight_layout()
    _save(fig, "03-hybrid.png")


# ============================================================
# Chart 4: Hallucination Risk Comparison Card
# ============================================================
def chart_hallucination() -> None:
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")

    # Title
    ax.text(5, 9.5, "Hallucination Risk: Grep+Read vs MCP",
            ha="center", va="top", fontsize=22, fontweight="bold", color=WHITE)

    # --- Left card: Grep+Read ---
    left_card = plt.Rectangle((0.3, 1.0), 4.2, 7.5, linewidth=2,
                               edgecolor=CORAL, facecolor=CARD_BG, zorder=2, alpha=0.95)
    ax.add_patch(left_card)
    ax.text(2.4, 8.0, "Grep + Read Agent", ha="center", fontsize=16,
            fontweight="bold", color=CORAL)

    left_items = [
        ("Tool calls per question", "2-3 avg", CORAL),
        ("Tokens per question", "1,600 avg", CORAL),
        ("Sees implementation", "Yes (noise)", CORAL),
        ("Wrong inferences", "Plausible", CORAL),
        ("Example", "", WHITE),
    ]
    for i, (label, val, col) in enumerate(left_items):
        yp = 7.2 - i * 1.1
        ax.text(0.6, yp, label, fontsize=12, color=GRAY, va="center")
        ax.text(4.2, yp, val, fontsize=13, fontweight="bold", color=col,
                ha="right", va="center")

    # Example text box
    example_y = 7.2 - 5 * 1.1 + 0.8
    ax.text(2.4, example_y + 0.5, "Agent sees:", fontsize=10, color=GRAY, ha="center")
    ax.text(2.4, example_y - 0.1,
            '_currentMode?.OnLevelCompleted(result)\n'
            '→ Infers "LevelCompletedEvent" exists\n'
            '→ Wrong: actual event is LevelWonEvent',
            fontsize=9, color=CORAL, ha="center", va="center",
            family="monospace",
            bbox=dict(boxstyle="round,pad=0.4", facecolor=BG, edgecolor=CORAL, alpha=0.8))

    # --- Right card: MCP ---
    right_card = plt.Rectangle((5.5, 1.0), 4.2, 7.5, linewidth=2,
                                edgecolor=CYAN, facecolor=CARD_BG, zorder=2, alpha=0.95)
    ax.add_patch(right_card)
    ax.text(7.6, 8.0, "MCP Agent", ha="center", fontsize=16,
            fontweight="bold", color=CYAN)

    right_items = [
        ("Tool calls per question", "1", CYAN),
        ("Tokens per question", "140 avg", CYAN),
        ("Sees implementation", "No (API only)", CYAN),
        ("Wrong inferences", "Eliminated", GREEN),
        ("Example", "", WHITE),
    ]
    for i, (label, val, col) in enumerate(right_items):
        yp = 7.2 - i * 1.1
        ax.text(5.8, yp, label, fontsize=12, color=GRAY, va="center")
        ax.text(9.4, yp, val, fontsize=13, fontweight="bold", color=col,
                ha="right", va="center")

    ax.text(7.6, example_y + 0.5, "Agent sees:", fontsize=10, color=GRAY, ha="center")
    ax.text(7.6, example_y - 0.1,
            'void ReportLevelCompleted(LevelResult)\n'
            '→ No implementation to misinterpret\n'
            '→ search("LevelWon") → correct answer',
            fontsize=9, color=CYAN, ha="center", va="center",
            family="monospace",
            bbox=dict(boxstyle="round,pad=0.4", facecolor=BG, edgecolor=CYAN, alpha=0.8))

    fig.tight_layout()
    _save(fig, "04-hallucination.png")


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    print("Generating benchmark charts...")
    chart_total_tokens()
    chart_per_step()
    chart_hybrid()
    chart_hallucination()
    print("Done! 4 charts generated in docs/images/")
