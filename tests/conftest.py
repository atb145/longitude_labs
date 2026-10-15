"""The "Try with sample case" button opens the bundled SEC filings in the app. The UI tests use the small fictional
case in tests/fixtures/sample_case instead: its names and decisions are what they check."""
import os
import pathlib

os.environ.setdefault("ANONYMIZER_SAMPLES", str(pathlib.Path(__file__).resolve().parent / "fixtures" / "sample_case"))
