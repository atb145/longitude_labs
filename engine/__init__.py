"""Longitude Anonymizer engine — UI-independent, so it can sit behind an API later.

    from engine import Session
    s = Session.from_files([("a.txt", b"..."), ...], policy="Finance")
    result = s.run()                       # registry, rendered outputs, review queue
    s.decide("E|PERSON:mercer", "accept")  # decisions apply to every occurrence
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, replace

from .detect import Detection, build_analyzer, detect
from .extract import extract_document, extract_text
from .policy import Policy, available_policies, load_policy, load_policy_text
from .registry import Result, build, consistency_check, redaction_log, release_check, search

__all__ = ["Session", "Policy", "Result", "available_policies", "load_policy", "load_policy_text", "extract_text",
           "build", "redaction_log", "consistency_check", "release_check", "search", "Detection"]

_ANALYZERS: dict[str, object] = {}


def _code_version() -> str:
    """Fingerprint of the engine and policy files. Sessions created by other code are rebuilt (see upgrade)."""
    import hashlib
    from pathlib import Path
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for path in sorted(list(root.glob("*.py")) + list((root.parent / "policies").rglob("*.yaml"))):
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


_CODE_VERSION = _code_version()


def analyzer_for(policy: Policy):
    # Keyed by the recognizer definitions, so an uploaded pack with the same name gets its own analyzer.
    key = repr(policy.recognizers)
    if key not in _ANALYZERS:
        _ANALYZERS[key] = build_analyzer(policy)
    return _ANALYZERS[key]


@dataclass
class Session:
    VERSION = _CODE_VERSION  # changes with any engine/policy edit, so open sessions are rebuilt on update
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
    undo_stack: list = field(default_factory=list)  # [(what changed, ops before the change)] — one history

    # ---- undo: every change to the registry goes through _checkpoint, so ↶ Undo reverses the last one
    def _checkpoint(self, what: str) -> None:
        self.undo_stack.append((what, copy.deepcopy(self.ops)))
        del self.undo_stack[:-100]

    @property
    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    @property
    def last_change(self) -> str:
        return self.undo_stack[-1][0] if self.undo_stack else ""

    def undo(self) -> str | None:
        """Reverse the last change (any kind). Returns what was undone, or None."""
        if not self.undo_stack:
            return None
        what, ops = self.undo_stack.pop()
        self.ops = ops
        return what

    def retype(self, entity_key: str, new_type: str, note: str = "") -> None:
        """'This is a person, not a company': change an entity's type; its label is recomputed."""
        self._checkpoint(note or "Change type")
        self.ops.setdefault("retype", {})[entity_key] = new_type
        self.ops.get("labels", {}).pop(entity_key, None)

    @classmethod
    def from_texts(cls, docs: list[tuple[str, str]], policy: str | Policy = "Finance") -> "Session":
        if isinstance(policy, str):
            policy = load_policy(available_policies()[policy])
        names, texts = [n for n, _ in docs], [t for _, t in docs]
        sess = cls(names, texts, policy, detect(analyzer_for(policy), policy, texts), dict(policy.settings))
        sess.ops["parties"] = [dict(p) for p in policy.known_parties]  # saved "always hide" names come along
        return sess

    @classmethod
    def from_files(cls, files: list[tuple[str, bytes]], policy: str | Policy = "Finance") -> "Session":
        docs = [(n, extract_document(n, b)) for n, b in files]
        sess = cls.from_texts([(n, d.text) for n, d in docs], policy)
        sess.warnings = [d.warnings for _, d in docs]
        return sess

    def add_files(self, files: list[tuple[str, bytes]]) -> list[str]:
        """Add documents to this set. They join the same registry, so names already seen keep their pseudonyms
        and every earlier decision still applies. Returns the names as stored (duplicates get " (2)")."""
        # Freeze the pseudonyms already in use: a new file may reveal a role or a fuller name, but the
        # documents a reviewer has already seen (or approved) must not change underneath them.
        labels = self.ops.setdefault("labels", {})
        for e in self.run().entities:
            if e.label not in ("", "Kept") and not e.nick_target:
                labels.setdefault(e.key, e.label)
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

    def export_rules(self, name: str, note: str = "") -> str:
        """The customized rules as a policy file: the pack's own YAML plus your settings, rule switches and
        "always hide" names. Load it again (library or upload) to reuse it on any document set."""
        import yaml
        from datetime import date
        raw = copy.deepcopy(self.policy.source_raw or {})
        raw["name"] = " ".join(name.split()) or self.policy.name
        raw["description"] = note or f"Saved from {self.policy.name} on {date.today().isoformat()}."
        keep = ("date_mode", "placeholder_style", "role_labels", "public_companies", "generalize_addresses",
                "redact_organizations", "disabled")
        raw["settings"] = {**(raw.get("settings") or {}), **{k: self.settings[k] for k in keep if k in self.settings}}
        raw["known_parties"] = [dict(p) for p in self.ops.get("parties", [])]
        return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=120)

    def run(self) -> Result:
        """Build the result. Memoized on (documents, settings, ops): the UI asks for it many times per click."""
        key = json.dumps({"o": self.ops, "s": self.settings, "n": len(self.texts), "p": id(self.policy),
                          "d": len(self.detections)}, sort_keys=True, default=str)
        memo = self.__dict__.get("_memo")
        if memo and memo[0] == key:
            return memo[1]
        res = build(self.names, self.texts, self.detections, self.policy, self.settings, self.ops)
        self.__dict__["_memo"] = (key, res)
        return res

    def decide(self, item_key: str, action: str, value: str = "", note: str = "") -> None:
        self._checkpoint(note or {"accept": "Accept", "keep": "Keep as written", "edit": "Change"}.get(action, action))
        self.ops["decisions"][item_key] = {"action": action, "value": value}
        self.ops["history"].append(item_key)

    def redact(self, text: str, entity_type: str = "MANUAL", replacement: str = "", label: str = "",
               note: str = "") -> None:
        """Redact a phrase by hand. Applies to every occurrence in every document. ``label`` is a
        template like "Project codename {1}" for reviewer-defined types."""
        self._checkpoint(note or f"Redact “{' '.join(text.split())[:40]}”")
        self.ops.setdefault("manual", []).append(
            {"text": text, "type": entity_type, "replacement": replacement, "label": label})

    def rename(self, entity_key: str, label: str, note: str = "") -> None:
        """Give an entity a label of your choice ("Defendant", "Receiving company"); it becomes its pseudonym in
        every document. Raises ValueError if another entity already uses that label."""
        label = " ".join(label.split())
        taken = {e.label.lower(): e for e in self.run().entities if e.key != entity_key}
        if label.lower() in taken:
            raise ValueError(f"“{label}” is already the label of {taken[label.lower()].canonical}. Pick it from "
                             "the dropdown to treat them as the same, or choose another label.")
        self._checkpoint(note or f"Rename to “{label}”")
        self.ops.setdefault("labels", {})[entity_key] = label

    def set_parties(self, parties: list[dict], note: str = "") -> None:
        """Known parties ({"name", "type"}): always redacted with every variant (context tool)."""
        self._checkpoint(note or "Change known parties")
        self.ops["parties"] = [p for p in parties if str(p.get("name", "")).strip()]

    def add_party(self, name: str, entity_type: str = "ORGANIZATION") -> None:
        name = " ".join(name.split())
        if name and name.lower() not in {p["name"].lower() for p in self.ops.get("parties", [])}:
            self.set_parties(list(self.ops.get("parties", [])) + [{"name": name, "type": entity_type}],
                             note=f"Add known party “{name}”")

    def remove_party(self, name: str) -> None:
        self.set_parties([p for p in self.ops.get("parties", []) if p["name"].lower() != name.lower()],
                         note=f"Remove known party “{name}”")

    @classmethod
    def upgrade(cls, old) -> "Session":
        """Rebuild a session created by an older version of the app (kept in a viewer's browser session)."""
        from .policy import parse_policy
        raw = getattr(getattr(old, "policy", None), "raw", None)
        policy = parse_policy(raw, "Policy") if isinstance(raw, dict) else load_policy(available_policies()["Finance"])
        new = cls.from_texts(list(zip(old.names, old.texts)), policy)
        ops = dict(getattr(old, "ops", {}) or {})
        for k, default in (("decisions", {}), ("history", []), ("merges", []), ("splits", []), ("manual", []),
                           ("deleted", []), ("aliases", []), ("parties", []), ("labels", {}), ("retype", {})):
            ops.setdefault(k, default)
        new.ops = ops
        new.approved = dict(getattr(old, "approved", {}) or {})
        new.warnings = list(getattr(old, "warnings", None) or [[] for _ in old.names])
        new.rights_confirmed = bool(getattr(old, "rights_confirmed", False))
        new.undo_stack = list(getattr(old, "undo_stack", None) or [])
        return new

    def redact_as(self, entity_key: str, text: str, note: str = "") -> None:
        """Redact a phrase as another form of an existing entity (it takes that entity's pseudonym)."""
        self._checkpoint(note or f"Redact “{' '.join(text.split())[:40]}”")
        self.ops.setdefault("aliases", []).append((entity_key, text))

    def unredact(self, entity_key: str, note: str = "") -> None:
        """Remove a manual redaction (entity keys look like 'TYPE:manual:<phrase>')."""
        self._checkpoint(note or f"Show “{entity_key.split(':')[-1]}” as written")
        self.ops["manual"] = [m for m in self.ops.get("manual", [])
                              if f"{m.get('type') or 'MANUAL'}:manual:{' '.join(m['text'].split()).lower()}" != entity_key]
        self.ops["decisions"].pop("E|" + entity_key, None)

    def delete(self, entity_key: str, note: str = "") -> None:
        """Remove a false detection from the registry (e.g. a heading read as a company); its text stays."""
        self._checkpoint(note or f"Show “{entity_key.split(':')[-1].title()}” as written")
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

    def merge(self, src_key: str, dst_key: str, note: str = "") -> None:
        self._checkpoint(note or "Treat as the same")
        self.ops["merges"].append((src_key, dst_key))

    def split(self, entity_key: str, form: str, new_type: str | None = None, into: str | None = None,
              note: str = "") -> None:
        """Move every form written like ``form`` out of an entity: into a new entry (optionally of another
        type) or into an existing entity ``into``."""
        self._checkpoint(note or f"Split off “{form}”")
        self.ops["splits"].append((entity_key, form, new_type, into))
