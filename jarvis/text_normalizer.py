from __future__ import annotations

import re


# Symbols that make sense on screen but sound terrible when handed directly
# to a TTS engine.  Keep this module deliberately deterministic: the LLM does
# not get another chance to rewrite its own answer just for speech.

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_FENCED_CODE_RE = re.compile(r"```(?:[\w.+-]+)?\n?(.*?)```", re.DOTALL)
_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]+)\]\(([^)]+)\)")
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "]+",
    flags=re.UNICODE,
)


def clean_for_display(text: str) -> str:
    """Turn common Markdown into clean plain text for RichLog.

    RichLog is not a Markdown renderer, so showing literal ** and backticks is
    uglier than just presenting the answer as readable plain text.
    """
    if not text:
        return ""

    out = text.replace("\r\n", "\n")

    # Keep code content on screen, only remove the fence markers.
    out = _FENCED_CODE_RE.sub(lambda m: m.group(1).strip("\n"), out)
    out = _MARKDOWN_LINK_RE.sub(lambda m: m.group(1), out)

    # Headings -> ordinary text.
    out = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", out)

    # Markdown bullets -> a normal readable bullet.
    out = re.sub(r"(?m)^\s*[-*+]\s+", "• ", out)

    # Bold/italic/strikethrough/code delimiters.
    out = out.replace("**", "").replace("__", "").replace("~~", "")
    out = out.replace("`", "")

    # Single emphasis asterisks. This intentionally removes the delimiter,
    # not the surrounding text.
    out = re.sub(r"(?<!\*)\*(?!\*)", "", out)

    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def clean_for_speech(text: str) -> str:
    """Prepare an LLM answer for Russian speech synthesis.

    Goals:
    - never pronounce Markdown punctuation;
    - do not read URLs or code syntax character by character;
    - make numeric ranges such as 7-9 hours sound natural;
    - avoid reading '/' as 'косая черта'.
    """
    if not text:
        return ""

    out = text.replace("\r\n", "\n")

    # Code blocks are useful on screen and nearly useless when spoken aloud.
    out = _FENCED_CODE_RE.sub(" Код приведён в сообщении. ", out)

    # Speak link labels, not the URLs themselves.
    out = _MARKDOWN_LINK_RE.sub(lambda m: m.group(1), out)
    out = _URL_RE.sub(" ссылка ", out)

    # Remove Markdown structure before dealing with punctuation.
    out = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", out)
    out = re.sub(r"(?m)^\s*[-*+]\s+", ". ", out)
    out = out.replace("**", "").replace("__", "").replace("~~", "")
    out = out.replace("`", "")
    out = re.sub(r"(?<!\*)\*(?!\*)", "", out)

    # Numeric ranges: 7-9 / 7–9 / 7—9 -> "от 7 до 9".
    # Piper handles Russian cardinal numbers much better than a hyphen that it
    # may interpret as punctuation or minus.
    out = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)\b", r"от \1 до \2", out)

    # Common symbols in assistant answers.
    out = re.sub(r"(\d+(?:[.,]\d+)?)\s*%", r"\1 процентов", out)
    out = out.replace("°C", " градусов Цельсия")
    out = out.replace("°", " градусов ")
    out = out.replace("&", " и ")
    out = out.replace("=", " равно ")

    # Slash should almost never be verbalized literally in an assistant reply.
    # Preserve meaning in common 'A/B' forms by turning it into 'A или B'.
    out = re.sub(r"(?<=\w)\s*/\s*(?=\w)", " или ", out)
    out = out.replace("/", " ")
    out = out.replace("\\", " ")

    # File-system-ish fragments are not pleasant to spell aloud. Keep ordinary
    # words but suppress long path punctuation.
    out = re.sub(r"(?<!\w)(?:~|/)[\w.\-/]+", " путь указан в сообщении ", out)

    # Remove leftover table pipes and decorative characters.
    out = out.replace("|", ", ")
    out = out.replace("#", "")
    out = out.replace("_", " ")
    out = _EMOJI_RE.sub("", out)

    # Parentheses are normally handled as pauses better than spoken symbols,
    # but brackets/citations like [1] should disappear.
    out = re.sub(r"\[(?:\d+|[^\]]{0,3})\]", "", out)

    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\s*\n\s*", ". ", out)
    out = re.sub(r"(?:\.\s*){3,}", ". ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip(" .") + "." if out.strip(" .") else ""
