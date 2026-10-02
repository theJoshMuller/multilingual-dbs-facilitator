"""Exact official Bible reads via the existing server integration; no credential copy."""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path

from dbs_curriculum import DEFAULT_WAHA_ROOT, read_data

PASSAGE = "GEN.1.1-25"
QUESTIONS = ("f.001", "f.002", "f.003", "f.008", *(f"a.{i:03}" for i in range(1, 8)))


class ContentUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class Edition:
    version_id: int
    language: str
    abbreviation: str
    localized_abbreviation: str
    title: str


# These are selections, not assertions of access. Never silently substitute another ID.
EDITIONS = {
    "en": Edition(116, "en", "NLT", "NLT", "New Living Translation"),
    "tr": Edition(170, "tr", "TCL02", "TCL02", "Kutsal Kitap Yeni Çeviri"),
    "es": Edition(128, "es", "NVI-S", "NVI", "Nueva Versión Internacional 2025"),
}


def integration_path():
    configured = os.environ.get("YVP_SERVER_FILE", "")
    if not configured:
        raise ContentUnavailable("Set YVP_SERVER_FILE to the existing YouVersion server integration")
    return Path(configured)


def canonical_questions(language, *, root=None):
    root = root or Path(os.environ.get("WAHA_ROOT", str(DEFAULT_WAHA_ROOT)))
    locale = {"en": "eng", "tr": "tur", "es": "spa"}.get(language)
    if not locale:
        raise ContentUnavailable("Unsupported canonical question language")
    spoken = read_data(root, "translationsSpokenQuestion").get(locale, {})
    if any(
        not isinstance(spoken.get(q), str) or not spoken[q].strip() for q in QUESTIONS
    ):
        raise ContentUnavailable(
            f"Missing original canonical Waha questions for {language}"
        )
    return {q: spoken[q] for q in QUESTIONS}


def validate_scripture(edition, metadata, organization, index, passage):
    expected = {
        "id": edition.version_id,
        "language_tag": edition.language,
        "abbreviation": edition.abbreviation,
        "localized_abbreviation": edition.localized_abbreviation,
        "title": edition.title,
    }
    if any(metadata.get(field) != value for field, value in expected.items()):
        raise ContentUnavailable(
            "Bible metadata does not match the exact selected edition"
        )
    copyright_text = metadata.get("copyright")
    if not isinstance(copyright_text, str) or not copyright_text.strip():
        raise ContentUnavailable(
            "Selected Bible is missing required copyright attribution"
        )
    if (
        not metadata.get("organization_id")
        or organization.get("id") != metadata["organization_id"]
        or not isinstance(organization.get("name"), str)
        or not organization["name"].strip()
    ):
        raise ContentUnavailable(
            "Selected Bible is missing matching publisher attribution"
        )
    genesis = next((b for b in index.get("books", []) if b.get("id") == "GEN"), {})
    chapter = next((c for c in genesis.get("chapters", []) if c.get("id") == "1"), {})
    available = [v.get("passage_id") for v in chapter.get("verses", [])]
    if any(available.count(f"GEN.1.{i}") != 1 for i in range(1, 26)):
        raise ContentUnavailable(
            "Selected edition lacks verified Genesis 1:1–25 coverage"
        )
    if (
        passage.get("id") != PASSAGE
        or not isinstance(passage.get("content"), str)
        or not passage["content"].strip()
        or not passage.get("reference")
    ):
        raise ContentUnavailable(
            "Official passage response does not match requested Genesis 1:1–25"
        )
    return {
        "source": "official YouVersion Platform API",
        "version_id": edition.version_id,
        "language": edition.language,
        "edition_title": edition.title,
        "abbreviation": edition.localized_abbreviation,
        "publisher": organization["name"],
        "copyright": copyright_text,
        "passage_id": PASSAGE,
        "reference": passage["reference"],
        "content": passage["content"],
    }


def fetch_selected(language):
    edition = EDITIONS.get(language)
    if not edition:
        raise ContentUnavailable("Unsupported Bible selection")
    spec = importlib.util.spec_from_file_location(
        "dbs_existing_youversion", integration_path()
    )
    if spec is None or spec.loader is None:
        raise ContentUnavailable(
            "Existing YouVersion server integration is unavailable"
        )
    integration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(integration)
    key = (
        integration.read_key()
    )  # Read in place. Never write, log, return or expose it to JS.

    def get(target):
        result = integration.fetch_api(target, key)
        if result["status"] != 200:
            raise ContentUnavailable(
                f"{language} version {edition.version_id}: official API HTTP {result['status']}; no edition fallback"
            )
        if not isinstance(result.get("body"), dict):
            raise ContentUnavailable("Unexpected official Bible response")
        return result["body"]

    metadata = get(f"/bibles/{edition.version_id}")
    publisher_id = metadata.get("organization_id", "")
    organization = get(f"/organizations/{publisher_id}")
    index = get(f"/bibles/{edition.version_id}/index")
    passage = get(f"/bibles/{edition.version_id}/passages/{PASSAGE}?format=text")
    return validate_scripture(edition, metadata, organization, index, passage)
