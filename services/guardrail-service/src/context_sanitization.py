import re
import unicodedata

# Zero-width and invisible characters sometimes used to hide injected text.
_INVISIBLE_CHARS = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")

# Markers that commonly wrap injected instructions in pasted content.
_INJECTION_WRAPPERS = re.compile(
    r"(system\s*:|assistant\s*:|<\|.*?\|>|\[INST\]|\[/INST\])", re.IGNORECASE
)

MAX_MESSAGE_LENGTH = 4000


class ContextSanitizer:
    def sanitize(self, raw_message: str) -> str:
        message = self._normalize_unicode(raw_message)
        message = self._strip_invisible_chars(message)
        message = self._neutralize_injection_wrappers(message)
        message = self._truncate(message)
        return message.strip()

    def _normalize_unicode(self, text: str) -> str:
        # Collapses homoglyphs/combining characters sometimes used to evade
        # keyword-based filters (e.g. "ｉｇｎｏｒｅ" -> "ignore").
        return unicodedata.normalize("NFKC", text)

    def _strip_invisible_chars(self, text: str) -> str:
        return _INVISIBLE_CHARS.sub("", text)

    def _neutralize_injection_wrappers(self, text: str) -> str:
        # Doesn't delete the content — just breaks the pattern that would
        # make an LLM interpret it as a role marker or special token.
        return _INJECTION_WRAPPERS.sub(lambda m: f"[{m.group(0)}]", text)

    def _truncate(self, text: str) -> str:
        if len(text) > MAX_MESSAGE_LENGTH:
            return text[:MAX_MESSAGE_LENGTH]
        return text
