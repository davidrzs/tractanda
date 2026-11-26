import pytest

from opencommune import build, scope
from tests.scope_data import A, D, serving_db, text_db


@pytest.mark.parametrize(
    ("text", "joined"),
    [
        ("Gemeinde-\nrat", "Gemeinderat"),
        ("Ver-\nwal-\ntung", "Verwaltung"),
        ("Abstim-\nmung über die Vor-\nlage", "Abstimmung über die Vorlage"),
        ("Gebüh-\nren und Ä-\nnderung", "Gebühren und Änderung"),
        ("crédit d'enga-\ngement", "crédit d'engagement"),
        ("é-\nécole", "éécole"),
        # an upper-case continuation is a new word: a compound or a name
        ("Gemeinde-\nRat", "Gemeinde-\nRat"),
        ("Gemeinde-\nÄmter", "Gemeinde-\nÄmter"),
        # no letter on either side, or not exactly a hyphen and one line break
        ("2025-\n2026", "2025-\n2026"),
        ("Budget-\n2026", "Budget-\n2026"),
        ("Seite 3 -\nweiter", "Seite 3 -\nweiter"),
        ("-\nrat", "-\nrat"),
        ("Gemeinde-\n", "Gemeinde-\n"),
        ("Gemeinde- \nrat", "Gemeinde- \nrat"),
        ("Gemeinde-\n\nrat", "Gemeinde-\n\nrat"),
        ("Gemeinde–\nrat", "Gemeinde–\nrat"),
        ("Gemeinde-rat", "Gemeinde-rat"),
        # a hyphen before und, oder, bis, bzw or sowie shortens a compound
        ("Ein-\nund Ausgaben", "Ein-\nund Ausgaben"),
        ("Hoch-\noder Tiefbau", "Hoch-\noder Tiefbau"),
        ("Primar-\nbzw. Sekundarschule", "Primar-\nbzw. Sekundarschule"),
        ("Gemeinde-\nunderlage", "Gemeindeunderlage"),
    ],
)
def test_words_split_at_a_line_end_are_joined(text, joined):
    assert scope.join_split_words(text) == joined


def test_the_types_are_those_of_deliberation_money_and_proposals():
    families = {"deliberation", "money", "proposals"}
    assert set(scope.TYPES) == {t for t, f in build.FAMILIES.items() if f in families}


def test_pages_lists_every_page_in_scope_with_its_text(tmp_path):
    serving = serving_db(tmp_path / "serving.db")
    text = text_db(tmp_path / "text.db")
    pages = list(scope.pages(serving, text))
    assert [(p.sha256, p.page, p.types, p.source, p.usable) for p in pages] == [
        (A, 1, ("budget",), "ocr", True),
        (A, 2, ("budget", "minutes"), "layer", True),
        (A, 3, ("minutes",), "layer", False),
        (A, 6, ("proposal",), "layer", True),
        (A, 7, ("proposal",), None, False),
        (D, 1, ("decisions",), "layer", True),
    ]
    assert pages[1].text == "Der Gemeinderat beschliesst den Steuerfuss.\nGemeinde-\nRat"
    assert pages[1].raw.startswith("Der Gemeinde-\nrat") and pages[4].text == ""


def test_an_ocr_answer_is_read_as_plain_text():
    answer = (
        '<div style="text-align: center;"><img src="imgs/img_in_header_image_box_88_51_264_143.jpg"'
        ' alt="Image" width="14%" />\n\nGEMEINDE BEISPIEL\n\n</div>\n\n\n## Rechnung 2023\n\n'
        "Laut Gesetz vom 1<sup>er</sup> Juli, **genehmigt**.<br/>Kontakt: <info@example.ch>\n\n"
        "<table border=1 style='margin: auto; word-wrap: break-word;'><tr>"
        '<td rowspan="2" colspan="2">Konto</td><td>Betrag $ ^{*} $</td></tr>'
        "<tr><td style='text-align: center;'>Unterhalt &amp; Reparatur</td>"
        "<td>19&#x27;983.60</td></tr></table>\n\n\n\nSchluss"
    )
    plain = scope.ocr_plain_text(answer)
    assert plain == (
        "GEMEINDE BEISPIEL\n\nRechnung 2023\n\nLaut Gesetz vom 1er Juli, genehmigt.\n"
        "Kontakt: <info@example.ch>\n\nKonto | Betrag *\nUnterhalt & Reparatur | 19'983.60\n\n"
        "Schluss"
    )
    page = scope.Page("a" * 64, 1, ("accounts",), "ocr", "ocr:q", answer)
    assert page.text == plain and page.usable
    layer = scope.Page("a" * 64, 1, ("accounts",), "layer", "layer:p", "<b>wie es ist</b>")
    assert layer.text == "<b>wie es ist</b>"
    tags_only = scope.Page(
        "a" * 64, 1, ("accounts",), "ocr", "ocr:q", "<table><tr><td></td></tr></table>" * 5
    )
    assert not tags_only.usable
