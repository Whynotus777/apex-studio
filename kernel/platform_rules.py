from __future__ import annotations

from pathlib import Path

_APEX_HOME = Path(__file__).resolve().parents[1]
_WRITER_CONSTRAINTS = _APEX_HOME / "templates" / "content-engine" / "agents" / "writer" / "constraints"

_INSTAGRAM_WRITER_RULES = """# Instagram Writer Rules

## Format
- Write caption-first content that is easy to scan on mobile.
- Open with a visual or emotional hook in the first line.
- Keep the core caption concise unless long-form storytelling is explicitly requested.
- Suggest a carousel, image, or reel concept when that improves performance.

## Tone
- Clear, polished, human, and visually aware.
- Favor save-worthy specificity over generic inspiration.
- Use line breaks intentionally to create rhythm.

## CTA
- End with one simple action: save, share, DM, or comment.
- Do not stack multiple CTAs in the same caption.

## Anti-Patterns
- Do not write like LinkedIn pasted into Instagram.
- Do not overload captions with hashtags or filler.
- Do not use vague aesthetic language without a concrete point.
"""

_CRITIC_ADJUSTMENTS = {
    "linkedin": (
        "Platform adjustment: weigh depth of insight, credibility, and evidence-backed specificity higher. "
        "A strong LinkedIn draft should teach, differentiate, and sound operator-grade."
    ),
    "x": (
        "Platform adjustment: weigh punchiness, compression, and hook sharpness higher. "
        "A strong X draft should land fast, create tension, and feel quotable."
    ),
    "tiktok": (
        "Platform adjustment: weigh hook strength, retention design, and spoken clarity higher. "
        "A strong TikTok draft should earn the first second and maintain momentum throughout."
    ),
    "instagram": (
        "Platform adjustment: weigh visual hook strength, save-worthiness, and caption rhythm higher. "
        "A strong Instagram draft should feel native to a visual feed and easy to revisit."
    ),
}


def _read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Platform rules file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def get_writer_instructions(platform: str) -> str:
    platform_key = platform.lower().strip()
    if platform_key == "linkedin":
        return _read_text(_WRITER_CONSTRAINTS / "soft-preferences.md")
    if platform_key in {"x", "twitter"}:
        return _read_text(_WRITER_CONSTRAINTS / "x-rules.md")
    if platform_key == "tiktok":
        return _read_text(_WRITER_CONSTRAINTS / "tiktok-rules.md")
    if platform_key == "instagram":
        return _INSTAGRAM_WRITER_RULES.strip()
    raise ValueError(f"Unsupported platform for writer instructions: {platform}")


def get_critic_adjustments(platform: str) -> str:
    platform_key = platform.lower().strip()
    if platform_key == "twitter":
        platform_key = "x"
    return _CRITIC_ADJUSTMENTS.get(
        platform_key,
        "Platform adjustment: preserve grounding and clarity, then optimize for native engagement on the destination platform.",
    )
