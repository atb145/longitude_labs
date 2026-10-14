"""What leaves the building. The buyer gets documents only; the mapping key stays internal.

While the redaction log (original → replacement) exists, the output is pseudonymized, not anonymized,
so it is never packed next to the documents (edge case 4.1).
"""
from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from .registry import Result, redaction_log


def buyer_zip(names: list[str], outputs: list[str]) -> bytes:
    """Anonymized documents only — no redaction log, no mapping key."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, out in zip(names, outputs):
            zf.writestr(f"{Path(name).stem}.anonymized.txt", out)
    return buf.getvalue()


def internal_log_csv(res: Result, only: list[str] | None = None) -> str:
    """The mapping key (original → replacement). Internal and access-controlled; never shipped to a buyer."""
    rows = redaction_log(res)
    if only:
        rows = [r for r in rows if any(n in r["documents"] for n in only)]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else ["original"])
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()
