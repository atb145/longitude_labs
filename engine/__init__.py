"""Longitude Anonymizer engine — UI-independent, so it can sit behind an API later.

    from engine import Session
    s = Session.from_files([("a.txt", b"..."), ...], policy="Finance")
    result = s.run()                       # registry, rendered outputs, review queue
    s.decide("E|PERSON:mercer", "accept")  # decisions apply to every occurrence
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .detect import Detection, build_analyzer, detect
from .extract import extract_document, extract_text
from .policy import Policy, available_policies, load_policy, load_policy_text
from .registry import Result, build, consistency_check, redaction_log, release_check

__all__ = ["Session", "Policy", "Result", "available_policies", "load_policy", "load_policy_text", "extract_text",
           "build", "redaction_log", "consistency_check", "release_check", "Detection"]

_ANALYZERS: dict[str, object] = {}


def analyzer_for(policy: Policy):
    # Keyed by the recognizer definitions, so an uploaded pack with the same name gets its own analyzer.
    key = repr(policy.recognizers)
    if key not in _ANALYZERS:
        _ANALYZERS[key] = build_analyzer(policy)
    return _ANALYZERS[key]


@dataclass
class Session:
    names: list[str]
    texts: list[str]
    policy: Policy
    detections: list[Detection]
    settings: dict
    ops: dict = field(default_factory=lambda: {"decisions": {}, "history": [], "merges": [], "splits": [], "manual": [],
                                               "deleted": []})
    warnings: list = field(default_factory=list)  # per document: extraction warnings ({"level", "message"})
    rights_confirmed: bool = False
    approved: dict = field(default_factory=dict)  # doc index -> {"output", "at", "changes"} snapshot

    @classmethod
    def from_texts(cls, docs: list[tuple[str, str]], policy: str | Policy = "Finance") -> "Session":
        if isinstance(policy, str):
            policy = load_policy(available_policies()[policy])
        names, texts = [n for n, _ in docs], [t for _, t in docs]
        return cls(names, texts, policy, detect(analyzer_for(policy), policy, texts), dict(policy.settings))

    @classmethod
    def from_files(cls, files: list[tuple[str, bytes]], policy: str | Policy = "Finance") -> "Session":
        docs = [(n, extract_document(n, b)) for n, b in files]
        sess = cls.from_texts([(n, d.text) for n, d in docs], policy)
        sess.warnings = [d.warnings for _, d in docs]
        return sess

    def add_files(self, files: list[tuple[str, bytes]]) -> list[str]:
        """Add documents to this set. They join the same registry, so names already seen keep their pseudonyms
        and every earlier decision still applies. Returns the names as stored (duplicates get " (2)")."""
        added = []
        offset = len(self.texts)
        new_texts = []
        for name, data in files:
            ex = extract_document(name, data)
            stored, n = name, 2
            while stored in self.names or stored in added:
                stored, n = f"{name} ({n})", n + 1
            added.append(stored)
            new_texts.append(ex.text)
            self.warnings.append(ex.warnings)
        dets = detect(analyzer_for(self.policy), self.policy, new_texts)
        self.detections = self.detections + [replace(d, doc=d.doc + offset) for d in dets]
        self.names = self.names + added
        self.texts = self.texts + new_texts
        return added

    def with_policy(self, policy: Policy) -> "Session":
        """Same documents and review decisions, re-detected under another policy pack."""
        new = Session(self.names, self.texts, policy, detect(analyzer_for(policy), policy, self.texts),
                      dict(policy.settings))
        new.ops = self.ops
        new.approved = self.approved
        new.warnings = self.warnings
        new.rights_confirmed = self.rights_confirmed
        return new

    def run(self) -> Result:
        return build(self.names, self.texts, self.detections, self.policy, self.settings, self.ops)

    def decide(self, item_key: str, action: str, value: str = "") -> None:
        self.ops["decisions"][item_key] = {"action": action, "value": value}
        self.ops["history"].append(item_key)

    def undo(self) -> str | None:
        if not self.ops["history"]:
            return None
        key = self.ops["history"].pop()
        self.ops["decisions"].pop(key, None)
        return key

    def redact(self, text: str, entity_type: str = "MANUAL", replacement: str = "", label: str = "") -> None:
        """Redact a phrase by hand. Applies to every occurrence in every document. ``label`` is a
        template like "Project codename {1}" for reviewer-defined types."""
        self.ops.setdefault("manual", []).append(
            {"text": text, "type": entity_type, "replacement": replacement, "label": label})

    def redact_as(self, entity_key: str, text: str) -> None:
        """Redact a phrase as another form of an existing entity (it takes that entity's pseudonym)."""
        self.ops.setdefault("aliases", []).append((entity_key, text))

    def unredact(self, entity_key: str) -> None:
        """Remove a manual redaction (entity keys look like 'TYPE:manual:<phrase>')."""
        self.ops["manual"] = [m for m in self.ops.get("manual", [])
                              if f"{m.get('type') or 'MANUAL'}:manual:{' '.join(m['text'].split()).lower()}" != entity_key]
        self.ops["decisions"].pop("E|" + entity_key, None)

    def delete(self, entity_key: str) -> None:
        """Remove a false detection from the registry (e.g. a heading read as a company); its text stays."""
        self.ops.setdefault("deleted", []).append(entity_key)
        self.ops["decisions"].pop("E|" + entity_key, None)

    def release_blockers(self, result: Result) -> list[str]:
        return release_check(result, self.warnings or [[] for _ in self.names], self.rights_confirmed)

    def approve(self, doc: int, result: Result) -> None:
        from datetime import datetime
        self.approved[doc] = {"output": result.outputs[doc], "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                              "changes": len(result.segments[doc])}

    def unapprove(self, doc: int) -> None:
        self.approved.pop(doc, None)

    def merge(self, src_key: str, dst_key: str) -> None:
        self.ops["merges"].append((src_key, dst_key))

    def split(self, entity_key: str, form: str, new_type: str | None = None, into: str | None = None) -> None:
        """Move every form written like ``form`` out of an entity: into a new entry (optionally of another
        type) or into an existing entity ``into``."""
        self.ops["splits"].append((entity_key, form, new_type, into))
