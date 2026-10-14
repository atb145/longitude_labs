---
title: Longitude Anonymizer
emoji: 📄
colorFrom: blue
colorTo: gray
sdk: streamlit
sdk_version: 1.65.0
python_version: "3.12"
app_file: app.py
pinned: false
---

# Longitude Anonymizer — prototype

Data partners upload a set of documents and get them back **consistently anonymized**: the same
real person, company or account gets the same pseudonym in every file ("Person A", "Company B",
"Account 1"). Partners use it to test our anonymization on their own documents before selling us
expert documents for AI training.

The engine is vertical-agnostic. This demo ships with a **Finance** policy pack and a three-document
sample case (placeholder text, one fictional enforcement matter).

## How it works

1. **Upload** a set of `.pdf`, `.docx` or `.txt` files (or press *Try with sample case*). Files are
   read in memory; the app never writes uploads to disk.
2. **Detect** — Microsoft Presidio with spaCy `en_core_web_lg`, organization detection switched on,
   plus regex recognizers defined in the policy pack (account, CRD, CUSIP, ticker, case/docket
   numbers, street addresses, dates, company-suffix names).
3. **Cross-document registry** — one shared map for the whole set. Variants resolve to one entity:
   full name, surname alone, honorifics (Mr./Ms./Dr.), middle initials, possessives ("Smith's"),
   ALL-CAPS captions, names broken across PDF lines, and defined-term aliases
   (`Meyer Global Management LLC (“MGM”)`). A surname shared by two people (David and Justine
   Reichman) is never linked on its own — only through an alias the document defines (“J. Reichman”).
4. **Confidence tiers** — clear-cut detections (emails, account numbers, SSNs, full names, suffixed
   company names) are applied automatically. Low-confidence detections and every relationship
   phrase go to the **review queue**.
5. **Review** one card at a time: Accept / Keep original / Edit replacement / Back (undo). A decision
   applies to every occurrence across all documents.
6. **Documents** — side by side (original ↔ anonymized, or any two documents), optional synced
   scrolling, hover any highlight to see the other version. Download all as a zip.
7. **Log & consistency** — downloadable CSV redaction log (original, replacement, type, documents,
   rule, decision) and a check that every entity got one replacement everywhere and that no original
   text survives in the output.

## Project layout

```
app.py              Streamlit UI (presentation only)
engine/             Anonymization engine — no Streamlit imports, ready to sit behind an API
  extract.py        PDF / DOCX / TXT → text, in memory
  detect.py         Presidio analyzer + policy recognizers
  registry.py       cross-document registry, policy application, rendering, log, consistency check
  policy.py         loads policies/*.yaml
  __init__.py       Session facade: from_files() → run() / decide() / undo() / merge() / split()
policies/
  finance.yaml      keep_list, always_redact, generalize, flag_for_review, edge_cases
samples/            bundled sample case
.streamlit/         theme (light + dark)
```

### Using the engine without the UI

```python
from engine import Session, redaction_log, consistency_check

s = Session.from_files([("a.pdf", open("a.pdf", "rb").read())], policy="Finance")
result = s.run()
s.decide(result.queue[0], "accept")       # applies to every occurrence
print(consistency_check(s.run()))
```

### Adding a policy pack

Copy `policies/finance.yaml` to e.g. `policies/legal.yaml`, change `name:` and the lists. It appears in
the policy selector automatically. Extra rules go under `edge_cases:` (`action: redact | flag | keep`).

## Run locally

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy to Hugging Face Spaces

1. Create a Space at <https://huggingface.co/new-space>: SDK **Streamlit**, hardware **CPU basic**
   (free tier is enough; spaCy `en_core_web_lg` needs ~1 GB RAM). Visibility **Private** while testing.
2. Push this repository to the Space:
   ```bash
   git remote add space https://huggingface.co/spaces/<your-org>/longitude-anonymizer
   git push space HEAD:main
   ```
   (Use a Hugging Face access token with *write* scope as the password.)
3. The YAML block at the top of this README configures the Space (`sdk: streamlit`, `app_file: app.py`).
   `requirements.txt` installs Presidio and the spaCy model wheel directly, so no extra build step is
   needed. First build takes ~5 minutes.
4. Light/dark: viewers switch in the app menu (⋮ → Settings → Theme).

## Production roadmap (out of scope for this prototype)

- **Access control:** sign-in with two-factor authentication, restricted to verified partner company
  email domains; SSO (SAML/OIDC) where the partner supports it.
- **Data handling:** automatic deletion of uploads and outputs after processing; per-partner data
  isolation (separate storage, keys and processing workers); audit log of who accessed what.
- **More policy packs:** legal, life sciences, consulting.
- Engine behind an authenticated API; batch processing for large sets; OCR for scanned PDFs.
