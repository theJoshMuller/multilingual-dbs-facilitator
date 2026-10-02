"""Read exact Waha curriculum; never ask a model to reconstruct Scripture."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

LESSON_ID = "01.001.001"
PASSAGE = "GEN.1.1-GEN.1.25"
DEFAULT_WAHA_ROOT = Path(__file__).resolve().parent.parent / "waha-app"


@dataclass
class Lesson:
    language_id: str
    language_name: str
    steps: list[str]
    questions: dict[str, str]
    verses: list[dict[str, str]]
    bible: str
    copyright: str
    scripture_url: str = ""

    @property
    def scripture(self) -> str:
        return " ".join(v["text"].strip() for v in self.verses)


def read_data(root: Path, name: str):
    return json.loads((root / "shared/data" / name / f"{name}.json").read_text())["data"]


def resolve_language(root: Path, code: str, waha_language: str = "") -> dict:
    languages = read_data(root, "languages")
    selected = waha_language or {"en": "eng", "es": "spa"}.get(code, "")
    if selected:
        matches = [item for item in languages if item["languageId"] == selected]
    else:
        matches = [item for item in languages if code in (
            item["languageId"], item.get("web", {}).get("code"),
            item.get("crowdinId", "").split("-")[0],
        )]
    if len(matches) != 1:
        raise ValueError(f"Choose WAHA_LANGUAGE explicitly for {code}; found {len(matches)} matches")
    return matches[0]


def validate_verses(verses: list[dict]) -> list[dict[str, str]]:
    expected = [f"GEN.1.{i}" for i in range(1, 26)]
    if [v.get("verseId") for v in verses] != expected:
        raise ValueError("Scripture must contain exactly GEN.1.1 through GEN.1.25 in order")
    if any(not isinstance(v.get("text"), str) or not v["text"].strip() for v in verses):
        raise ValueError("Every Scripture verse must have source text")
    return [{"verseId": v["verseId"], "text": v["text"]} for v in verses]


def load_lesson(
    code: str = "en", *, root: Path | None = None,
    waha_language: str = "", scripture_file: Path | None = None,
) -> Lesson:
    root = root if root is not None else Path(os.getenv('WAHA_ROOT', str(DEFAULT_WAHA_ROOT)))
    language = resolve_language(root, code, waha_language)
    lesson = next(
        l for s in read_data(root, "sets") for l in s["lessons"]
        if l["lessonId"] == LESSON_ID
    )
    if lesson["s"] != [PASSAGE]:
        raise ValueError("Canonical lesson passage changed; review before running")
    curriculum_id = language.get("curriculum", {}).get("questions", "eng")
    curriculum = next(c for c in read_data(root, "curriculumQuestions") if c["curriculumId"] == curriculum_id)
    question_sets = {s["questionSetId"]: s["questions"] for s in curriculum["questionSets"]}
    steps = [*question_sets[lesson["f"]], "scripture", *question_sets[lesson["a"]]]
    spoken = read_data(root, "translationsSpokenQuestion").get(language["languageId"], {})
    missing = [s for s in steps if s != "scripture" and not spoken.get(s)]
    if missing:
        raise ValueError(f"Missing canonical {language['languageId']} questions: {missing}")
    questions = {s: spoken[s] for s in steps if s != "scripture"}

    # Josh's prototype-specific selection overrides Waha's Spanish NBLA default.
    spanish = language["languageId"] in ("spa", "spn")
    bible = "NVI" if spanish else language.get("bible")
    if isinstance(bible, dict):
        bible = bible["bibleTextId"]
    if not isinstance(bible, str) or not bible:
        raise ValueError("No canonical Bible configured for this language")
    if language.get("bibleModifiers") and not spanish and scripture_file is None:
        raise ValueError("Bible modifiers require an explicit resolved SCRIPTURE_FILE in this prototype")
    copyright_text = next((b.get("copyright", "") for b in read_data(root, "bibleTexts") if b["bibleTextId"] == bible), "")
    verses = []
    url = "https://www.bible.com/es/bible/128/GEN.1.NVI" if spanish else ""
    if scripture_file:
        source = json.loads(scripture_file.read_text())
        if source.get("bibleTextId") != bible or source.get("languageId") != language["languageId"]:
            raise ValueError(f"SCRIPTURE_FILE must be {language['languageId']} {bible}, not another version")
        verses = validate_verses(source["verses"])
        copyright_text = source.get("copyright", copyright_text)
    elif not spanish:
        # Read user-owned Waha cache only. No network scraping or translation fallback.
        cache = root / ".cache/bible" / f"{bible}.json"
        if not cache.exists():
            raise ValueError(f"Missing canonical Bible cache: {cache}; supply SCRIPTURE_FILE")
        chapters = json.loads(cache.read_text())
        chapter = next(c for c in chapters if c["chapterId"] == "GEN.1")
        expected = {f"GEN.1.{i}" for i in range(1, 26)}
        verses = validate_verses([v for v in chapter["verses"] if v["verseId"] in expected])
    return Lesson(language["languageId"], language["exonym"], steps, questions, verses, bible, copyright_text, url)
