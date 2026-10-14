"""Policy packs: YAML files in /policies, one per vertical."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

POLICY_DIR = Path(__file__).resolve().parent.parent / "policies"


@dataclass
class FlagRule:
    id: str
    regex: re.Pattern
    replacement: str
    reason: str
    scope: str = "match"          # "match" flags the matched words; "sentence" flags the whole sentence


@dataclass
class Policy:
    name: str
    raw: dict
    settings: dict
    auto_apply: float
    min_detection: float
    entities: dict
    keep_terms: list[str]
    keep_patterns: list[re.Pattern]
    public_companies: list[str]
    not_entities: set[str]
    name_noise: set[str]
    recognizers: list[dict]
    flags: list[FlagRule] = field(default_factory=list)
    edge_rules: list[dict] = field(default_factory=list)
    nicknames: dict[str, list[str]] = field(default_factory=dict)
    quasi: dict = field(default_factory=dict)          # {"min_categories": int, "categories": {name: [regex]}}
    party_cues: dict = field(default_factory=dict)     # {"before": [regex], "after": [regex]}
    generic_domains: set[str] = field(default_factory=set)
    role_rules: list[dict] = field(default_factory=list)  # {"where", "regex", "role", "types"}

    def spec(self, entity_type: str) -> dict:
        return self.entities.get(entity_type, {"action": "ignore"})

    def buckets(self) -> list[tuple[str, str, str, str]]:
        """Rule buckets a viewer can switch on/off: (group, bucket id, label, help text).
        Bucket ids are what ``settings["disabled"]`` holds."""
        out = []
        for etype, spec in self.entities.items():
            action = spec.get("action")
            if action in ("redact", "generalize"):
                group = "Always redact" if action == "redact" else "Generalize"
                out.append((group, etype, spec.get("title") or etype.replace("_", " ").title(), spec.get("reason", "")))
        seen = set()
        edge_ids = {r["id"] for r in self.edge_rules}
        for f in self.flags:
            if f.id not in seen and f.id not in edge_ids:
                seen.add(f.id)
                out.append(("Flag for review", f.id, f.id.split(".", 1)[-1].replace("_", " ").capitalize(), f.reason))
        if self.quasi.get("categories"):
            out.append(("Flag for review", "quasi.combination", "Identifying combinations (age, role, place, date…)",
                        "A sentence combining several attributes can single someone out with no name in it."))
        for r in self.edge_rules:
            out.append(("Edge cases", r["id"], r.get("description") or r["id"], r.get("reason", "")))
        if self.keep_terms or self.keep_patterns:
            out.append(("Keep list", "keep_list", "Never redact public bodies (SEC, FINRA, courts…)",
                        "Off: keep-list terms are treated like any other text."))
        return out

    @property
    def detect_types(self) -> list[str]:
        return [t for t, s in self.entities.items() if s.get("action") != "ignore"] + ["LOCATION"]


def available_policies() -> dict[str, Path]:
    out = {}
    for path in sorted(p for p in POLICY_DIR.glob("*.yaml") if not p.name.startswith("_")):
        with open(path) as fh:
            out[yaml.safe_load(fh).get("name", path.stem)] = path
    return out


def load_policy(path: str | Path) -> Policy:
    with open(path) as fh:
        return parse_policy(yaml.safe_load(fh), Path(path).stem)


def load_policy_text(text: str, fallback_name: str = "Custom") -> Policy:
    """Parse an uploaded policy pack (in memory). Raises ValueError with a readable message."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Policy file is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict) or not raw.get("entities"):
        raise ValueError("Policy file needs at least an 'entities:' section (see policies/finance.yaml).")
    try:
        return parse_policy(raw, fallback_name)
    except re.error as exc:
        raise ValueError(f"A regex pattern in the policy is invalid: {exc}") from exc
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Policy file is missing a required field: {exc}") from exc


def _merge_base(raw: dict) -> dict:
    """General rules in policies/_base.yaml apply to every pack; the pack's own values win on conflicts."""
    base_path = POLICY_DIR / "_base.yaml"
    if raw.get("name") == "_base" or not base_path.exists():
        return raw
    with open(base_path) as fh:
        base = yaml.safe_load(fh) or {}
    out = dict(raw)
    out["settings"] = {**base.get("settings", {}), **raw.get("settings", {})}
    out["entities"] = {**base.get("entities", {}), **raw.get("entities", {})}
    for key in ("recognizers", "flag_for_review", "not_entities", "name_noise_words", "public_companies"):
        out[key] = list(base.get(key) or []) + list(raw.get(key) or [])
    keep_b, keep_r = base.get("keep_list") or {}, raw.get("keep_list") or {}
    out["keep_list"] = {k: list(keep_b.get(k) or []) + list(keep_r.get(k) or []) for k in ("terms", "patterns")}
    out["nicknames"] = {**(base.get("nicknames") or {}), **(raw.get("nicknames") or {})}
    out["quasi_identifiers"] = raw.get("quasi_identifiers") or base.get("quasi_identifiers") or {}
    cues_b, cues_r = base.get("party_cues") or {}, raw.get("party_cues") or {}
    out["party_cues"] = {k: list(cues_b.get(k) or []) + list(cues_r.get(k) or []) for k in ("before", "after")}
    out["generic_domains"] = list(base.get("generic_domains") or []) + list(raw.get("generic_domains") or [])
    out["role_labels"] = list(raw.get("role_labels") or []) + list(base.get("role_labels") or [])  # pack rules first
    return out


def parse_policy(raw: dict, fallback_name: str) -> Policy:
    raw = _merge_base(raw)
    recognizers = [dict(r) for r in raw.get("recognizers", [])]
    flags = [
        FlagRule(f["id"], re.compile(f["pattern"]), f.get("replacement", "[redacted]"), f["reason"],
                 f.get("scope", "match"))
        for f in raw.get("flag_for_review", [])
    ]
    keep = raw.get("keep_list", {})
    keep_terms = list(keep.get("terms", []))
    keep_patterns = [re.compile(p) for p in keep.get("patterns", [])]
    entities = dict(raw.get("entities", {}))

    # Edge-case rules plug into the same three mechanisms.
    for rule in raw.get("edge_cases") or []:
        action = rule.get("action")
        if action == "redact":
            etype = rule["entity"]
            recognizers.append({"entity": etype, "score": rule.get("score", 0.95),
                                "patterns": [rule["pattern"]], "name": rule["id"]})
            entities.setdefault(etype, {"action": "redact", "label": f"{etype.title()} {{1}}",
                                        "rule": rule["id"], "reason": rule.get("reason", ""), "auto": True})
        elif action == "flag":
            flags.append(FlagRule(rule["id"], re.compile(rule["pattern"]),
                                  rule.get("replacement", "[redacted]"), rule.get("reason", ""),
                                  rule.get("scope", "match")))
        elif action == "keep":
            keep_patterns.append(re.compile(rule["pattern"]))

    th = raw.get("thresholds", {})
    return Policy(
        name=raw.get("name", fallback_name),
        raw=raw,
        settings=dict(raw.get("settings", {})),
        auto_apply=float(th.get("auto_apply", 0.85)),
        min_detection=float(th.get("min_detection", 0.4)),
        entities=entities,
        keep_terms=keep_terms,
        keep_patterns=keep_patterns,
        public_companies=list(raw.get("public_companies", [])),
        not_entities={t.lower() for t in raw.get("not_entities", [])},
        name_noise={t.lower() for t in raw.get("name_noise_words", [])},
        recognizers=recognizers,
        flags=flags,
        edge_rules=[dict(r) for r in raw.get("edge_cases") or []],
        nicknames={k.lower(): [v.lower() for v in vs] for k, vs in (raw.get("nicknames") or {}).items()},
        party_cues={k: [re.compile(p) for p in v] for k, v in (raw.get("party_cues") or {}).items()},
        generic_domains={d.lower() for d in raw.get("generic_domains") or []},
        role_rules=[{"where": r.get("where", "after"), "regex": re.compile(r["pattern"]), "role": r.get("role"),
                     "types": set(r.get("types") or ["PERSON", "ORGANIZATION"])} for r in raw.get("role_labels") or []],
        quasi={"min_categories": int((raw.get("quasi_identifiers") or {}).get("min_categories", 3)),
               "categories": {k: [re.compile(p) for p in v]
                              for k, v in ((raw.get("quasi_identifiers") or {}).get("categories") or {}).items()}},
    )
