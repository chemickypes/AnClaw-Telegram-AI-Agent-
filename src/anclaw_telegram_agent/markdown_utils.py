"""Convert standard Markdown to Telegram format.

Telegram target syntax:
  **text**    → bold
  __text__    → italic
  `text`      → inline code
  ```text```  → code block
  [text](url) → hyperlink
"""

import re


def md_to_telegram(text: str) -> str:
    """Convert standard Markdown to Telegram bold/italic format."""
    # --- 1. Protect code blocks and inline code (don't touch their content) ---
    _code_blocks: list[str] = []
    _inline_codes: list[str] = []

    def _save_block(m: re.Match) -> str:
        _code_blocks.append(m.group(0))
        return f"\x00BLK{len(_code_blocks) - 1}\x00"

    def _save_inline(m: re.Match) -> str:
        _inline_codes.append(m.group(0))
        return f"\x00INL{len(_inline_codes) - 1}\x00"

    text = re.sub(r"```[\s\S]*?```", _save_block, text)
    text = re.sub(r"`[^`\n]+`", _save_inline, text)

    # --- 2. Bold: **text** already correct; __text__ (some MD flavors) → **text** ---
    # Use placeholder to prevent the italic pass from touching bold spans.
    text = re.sub(r"\*\*(.+?)\*\*", "\x01\\1\x01", text, flags=re.DOTALL)
    text = re.sub(r"(?<![_])__(.+?)__(?![_])", "\x01\\1\x01", text, flags=re.DOTALL)

    # --- 3. Headings → bold ---
    text = re.sub(r"^#{1,6}\s+(.+)$", "\x01\\1\x01", text, flags=re.MULTILINE)

    # --- 4. Italic: *text* or _text_ → __text__ ---
    text = re.sub(r"\*([^*\n]+?)\*", r"__\1__", text)
    text = re.sub(r"(?<![_])_([^_\n]+?)_(?![_])", r"__\1__", text)

    # --- 5. Restore bold placeholders → **text** ---
    text = text.replace("\x01", "**")

    # --- 6. Strikethrough: ~~text~~ → text (unsupported, strip markers) ---
    text = re.sub(r"~~(.+?)~~", r"\1", text, flags=re.DOTALL)

    # --- 7. Bullet points: - item / * item → • item ---
    text = re.sub(r"^[ \t]*[-*]\s+", "• ", text, flags=re.MULTILINE)

    # --- 8. Blockquotes: > text → text ---
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)

    # --- 9. Horizontal rules ---
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)

    # --- 10. Restore code ---
    for i, code in enumerate(_inline_codes):
        text = text.replace(f"\x00INL{i}\x00", code)
    for i, block in enumerate(_code_blocks):
        text = text.replace(f"\x00BLK{i}\x00", block)

    # --- 11. Collapse excess blank lines produced by removed elements ---
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()
