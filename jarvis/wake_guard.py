from __future__ import annotations

import re
import time

from rapidfuzz.fuzz import ratio

from .voice import VoiceListener


_WORD_RE = re.compile(r"[a-zа-яё]+", re.IGNORECASE)


def _candidate_for(listener: VoiceListener, text: str) -> tuple[str, bool] | None:
    """Return (wake_word, exact) only for whole-word, high-confidence matches.

    The old code used partial_ratio(wake, whole_partial_sentence), which can
    score absurdly high for unrelated short words. That is why JARVIS was
    waking up from normal speech every few seconds.
    """
    tokens = _WORD_RE.findall(text.casefold())
    if not tokens:
        return None

    # Existing configs may still contain several very permissive variants.
    # In strict mode we intentionally use a smaller safe set unless the user
    # explicitly provides wake_words_strict.
    wakes = listener.config.get("wake_words_strict") or ["джарвис", "жарвис", "jarvis"]
    wakes = [str(w).casefold().strip() for w in wakes if str(w).strip()]

    threshold = max(90, int(listener.config.get("wake_fuzzy_threshold", 94)))
    exact_only = bool(listener.config.get("wake_exact_only", True))

    for token in tokens[-4:]:
        for wake in wakes:
            if token == wake:
                return wake, True

    if exact_only:
        return None

    best: tuple[str, int] | None = None
    for token in tokens[-4:]:
        for wake in wakes:
            # Never fuzzy-match tiny fragments. Length must also be close.
            if len(token) < 5 or abs(len(token) - len(wake)) > 2:
                continue
            score = int(ratio(token, wake))
            if score >= threshold and (best is None or score > best[1]):
                best = (wake, score)

    return (best[0], False) if best else None


def _strict_match_wake(self: VoiceListener, text: str) -> bool:
    now = time.monotonic()
    cooldown = float(self.config.get("wake_cooldown_sec", 4.0))
    last_wake = float(getattr(self, "_strict_last_wake_at", 0.0))
    if now - last_wake < cooldown:
        return False

    candidate = _candidate_for(self, text)
    if candidate is None:
        self._strict_wake_candidate = None
        self._strict_wake_streak = 0
        return False

    wake, exact = candidate
    previous = getattr(self, "_strict_wake_candidate", None)
    streak = int(getattr(self, "_strict_wake_streak", 0))

    if previous == wake:
        streak += 1
    else:
        streak = 1
        self._strict_wake_candidate = wake

    self._strict_wake_streak = streak

    # Vosk partials are emitted repeatedly. Requiring the same whole wake word
    # several times costs ~60-120 ms but eliminates most accidental triggers.
    confirmations = max(2, int(self.config.get("wake_confirmations", 3)))
    if not exact:
        confirmations = max(confirmations, 4)

    if streak < confirmations:
        return False

    self._strict_last_wake_at = now
    self._strict_wake_candidate = None
    self._strict_wake_streak = 0
    return True


def install_strict_wake_word() -> None:
    if getattr(VoiceListener, "_stas_strict_wake_installed", False):
        return
    VoiceListener._match_wake = _strict_match_wake
    VoiceListener._stas_strict_wake_installed = True
