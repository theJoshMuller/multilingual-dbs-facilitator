import copy
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from dbs_youversion import (
    EDITIONS,
    QUESTIONS,
    ContentUnavailable,
    canonical_questions,
    integration_path,
    validate_scripture,
)


def fixture():
    metadata = {
        "id": 128,
        "language_tag": "es",
        "abbreviation": "NVI-S",
        "localized_abbreviation": "NVI",
        "title": "Nueva Versión Internacional 2025",
        "copyright": "Synthetic publisher copyright fixture",
        "organization_id": "publisher-test",
    }
    organization = {"id": "publisher-test", "name": "Synthetic Publisher"}
    index = {
        "books": [
            {
                "id": "GEN",
                "chapters": [
                    {
                        "id": "1",
                        "verses": [{"passage_id": f"GEN.1.{i}"} for i in range(1, 32)],
                    }
                ],
            }
        ]
    }
    passage = {
        "id": "GEN.1.1-25",
        "content": "Synthetic fixture; not Bible text",
        "reference": "Génesis 1:1-25",
    }
    return metadata, organization, index, passage


class ScriptureTests(unittest.TestCase):
    def test_exact_nvi_edition_attribution_and_genesis_coverage(self):
        result = validate_scripture(EDITIONS["es"], *fixture())
        self.assertEqual(result["version_id"], 128)
        self.assertEqual(result["edition_title"], "Nueva Versión Internacional 2025")
        self.assertEqual(result["publisher"], "Synthetic Publisher")
        self.assertEqual(result["content"], fixture()[-1]["content"])

    def test_wrong_id_language_edition_nbLa_and_missing_copyright_fail_closed(self):
        for field, value in (
            ("id", 2664),
            ("language_tag", "pt"),
            ("abbreviation", "NBLA"),
            ("title", "NVI 2015"),
            ("copyright", ""),
        ):
            data = list(copy.deepcopy(fixture()))
            data[0][field] = value
            with self.assertRaises(ContentUnavailable):
                validate_scripture(EDITIONS["es"], *data)

    def test_missing_verse_or_wrong_passage_or_empty_text_fail_closed(self):
        data = list(fixture())
        data[2]["books"][0]["chapters"][0]["verses"].pop(10)
        with self.assertRaises(ContentUnavailable):
            validate_scripture(EDITIONS["es"], *data)
        for field, value in (("id", "GEN.1"), ("content", "")):
            data = list(copy.deepcopy(fixture()))
            data[3][field] = value
            with self.assertRaises(ContentUnavailable):
                validate_scripture(EDITIONS["es"], *data)

    def test_publisher_identity_must_match(self):
        data = list(fixture())
        data[1]["id"] = "wrong"
        with self.assertRaises(ContentUnavailable):
            validate_scripture(EDITIONS["es"], *data)

    def test_canonical_turkish_questions_are_original_and_complete(self):
        original = {q: f"Original source fixture {q}" for q in QUESTIONS}
        with patch("dbs_youversion.read_data", return_value={"tur": original}):
            result = canonical_questions("tr", root=Path("/fixture/waha"))
        self.assertEqual(result, original)

    def test_integration_is_explicitly_configured_without_personal_default(self):
        with patch.dict(os.environ, {"YVP_SERVER_FILE": "/fixture/yvp/server.py"}):
            self.assertEqual(integration_path(), Path("/fixture/yvp/server.py"))
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ContentUnavailable):
            integration_path()

    def test_missing_canonical_question_fails_closed(self):
        with patch("dbs_youversion.read_data", return_value={"tur": {}}), self.assertRaises(ContentUnavailable):
            canonical_questions("tr", root=Path("/fixture/waha"))


if __name__ == "__main__":
    unittest.main()
