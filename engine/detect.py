"""Entity detection: Presidio + spaCy en_core_web_lg + policy-defined recognizers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from presidio_analyzer import AnalyzerEngine, EntityRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider

from .policy import Policy

SPACY_MODEL = "en_core_web_lg"


@dataclass(frozen=True)
class Detection:
    doc: int
    start: int
    end: int
    text: str
    type: str
    score: float
    source: str


class RegexGroupRecognizer(EntityRecognizer):
    """Regex recognizer whose reported span is the named group ``v`` (or the whole match)."""

    def __init__(self, entity: str, patterns: list[str], score: float, name: str | None = None):
        self.compiled = [re.compile(p) for p in patterns]
        self.score = score
        super().__init__(supported_entities=[entity], name=name or f"policy_{entity.lower()}")

    def load(self) -> None:  # nothing to load
        pass

    def analyze(self, text, entities, nlp_artifacts=None):
        results = []
        for rx in self.compiled:
            for m in rx.finditer(text):
                start, end = m.span("v") if "v" in rx.groupindex else m.span()
                if end <= start:
                    continue
                results.append(RecognizerResult(
                    entity_type=self.supported_entities[0], start=start, end=end, score=self.score,
                    recognition_metadata={RecognizerResult.RECOGNIZER_NAME_KEY: self.name,
                                          RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id},
                ))
        return results


@lru_cache(maxsize=1)
def _nlp_engine():
    conf = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": SPACY_MODEL}],
        "ner_model_configuration": {
            # Organization detection is off/weak in Presidio's defaults; map ORG explicitly
            # and do not down-weight it.
            "model_to_presidio_entity_mapping": {
                "PERSON": "PERSON", "PER": "PERSON", "ORG": "ORGANIZATION",
                "GPE": "LOCATION", "LOC": "LOCATION", "FAC": "LOCATION", "NORP": "NRP",
            },
            "low_score_entity_names": [],
            "labels_to_ignore": ["CARDINAL", "DATE", "TIME", "MONEY", "PERCENT", "QUANTITY",
                                 "ORDINAL", "LAW", "EVENT", "PRODUCT", "WORK_OF_ART", "LANGUAGE"],
        },
    }
    return NlpEngineProvider(nlp_configuration=conf).create_engine()


def build_analyzer(policy: Policy) -> AnalyzerEngine:
    analyzer = AnalyzerEngine(nlp_engine=_nlp_engine(), supported_languages=["en"])
    for spec in policy.recognizers:
        analyzer.registry.add_recognizer(
            RegexGroupRecognizer(spec["entity"], spec["patterns"], float(spec.get("score", 0.9)), spec.get("name")))
    return analyzer


# spaCy needs memory in proportion to the text it is given at once (a 230-page filing in one piece peaks
# above 3 GB), so long documents are analyzed in pieces of about this many characters.
CHUNK = 20_000


def chunks(text: str, size: int = CHUNK):
    """(offset, piece) pairs of at most ``size`` characters, cut at a blank line where possible, else at a line
    break or a space, so a name is never split between two pieces."""
    i, n = 0, len(text)
    while i < n:
        j = min(n, i + size)
        if j < n:
            for sep in ("\n\n", "\n", " "):
                cut = text.rfind(sep, i + size // 2, j)
                if cut != -1:
                    j = cut + len(sep)
                    break
        yield i, text[i:j]
        i = j


def detect(analyzer: AnalyzerEngine, policy: Policy, docs: list[str], progress=None) -> list[Detection]:
    """``progress(done, total)`` is called with characters analyzed so far, for a progress bar."""
    out: list[Detection] = []
    total, done = sum(len(t) for t in docs) or 1, 0
    for i, text in enumerate(docs):
        for offset, piece in chunks(text):
            results = analyzer.analyze(text=piece, language="en", entities=policy.detect_types,
                                       score_threshold=policy.min_detection)
            for r in results:
                source = (r.recognition_metadata or {}).get(RecognizerResult.RECOGNIZER_NAME_KEY, "")
                start, end = r.start + offset, r.end + offset
                out.append(Detection(i, start, end, text[start:end], r.entity_type, round(r.score, 2), source))
            done += len(piece)
            if progress:
                progress(done, total)
    return out
