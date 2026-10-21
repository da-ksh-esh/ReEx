"""Decides whether a review is English. The text model and VADER only understand English."""

import re

from langdetect import DetectorFactory, LangDetectException, detect_langs

DetectorFactory.seed = 0  # langdetect is random by default; make results repeatable

# langdetect is unreliable on very short text ("Nice" comes back as Romanian), so short
# Latin-script reviews are always kept.
MIN_WORDS_TO_DETECT = 4
MIN_CONFIDENCE = 0.9
# Reviews mostly in another script (Devanagari, Arabic, CJK...) are skipped at any length.
MAX_NON_LATIN_SHARE = 0.5

_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")


def is_english(text: str) -> bool:
    letters = _LETTER.findall(text)
    if not letters:
        return True
    non_latin = sum(1 for ch in letters if not _LATIN.match(ch))
    if non_latin / len(letters) > MAX_NON_LATIN_SHARE:
        return False
    if len(text.split()) < MIN_WORDS_TO_DETECT:
        return True
    try:
        top = detect_langs(text)[0]
    except LangDetectException:
        return True
    return top.lang == "en" or top.prob < MIN_CONFIDENCE
