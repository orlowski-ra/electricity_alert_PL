from datetime import datetime

from electricity_alert.models import Outage
from electricity_alert.text import (
    contains_loosely,
    fold,
    numbers_text,
    outage_body,
    range_text,
    street_key_word,
)


def test_numbers_joined_exactly_as_from_api():
    assert numbers_text(["5, 7", "numery: 9A-11"]) == "5, 7; numery: 9A-11"
    assert numbers_text([]) == ""


def test_range_texts():
    assert range_text(datetime(2026, 10, 14, 8), datetime(2026, 10, 14, 16)) == "14.10.2026 08:00 – 16:00"
    assert range_text(datetime(2026, 10, 14, 8), datetime(2026, 10, 15, 12, 30)) == "14.10.2026 08:00 – 15.10.2026 12:30"
    assert range_text(None, None) == "? – ?"
    assert range_text(None, datetime(2026, 10, 15, 12, 30)) == "? – 15.10.2026 12:30"


def test_notification_body():
    o = Outage("u1", ("5, 7", "9A"), datetime(2026, 10, 14, 8), datetime(2026, 10, 14, 16))
    assert outage_body(o) == "5, 7; 9A\n14.10.2026 08:00 – 16:00"
    assert outage_body(Outage("u1", (), o.start_at, o.stop_at)) == "14.10.2026 08:00 – 16:00"


def test_fold_and_loose_contains():
    assert fold("ZAŻÓŁĆ GĘŚLĄ JAŹŃ") == "zazolc gesla jazn"
    assert contains_loosely("ul. Marszałkowska", "szalk")
    assert contains_loosely("ul. Marszałkowska", "MARSZAŁ")
    assert contains_loosely("Stefana Żeromskiego", "zeromsk")
    assert not contains_loosely("ul. Marszałkowska", "marszalek")


def test_street_key_word():
    assert street_key_word("ul. Stefana Żeromskiego") == "Żeromskiego"
    assert street_key_word("Górnicza") == "Górnicza"
    assert street_key_word("al. Jana Pawła II") == "Pawła"
    assert street_key_word("ul. 23 Lutego") == "Lutego"
    assert street_key_word("70 Pułku Piechoty") == "Piechoty"
    assert street_key_word('ul. Podpułkownika Jerzego Dąbrowskiego ps. "Łupaszka"') == "Dąbrowskiego"
    assert street_key_word("ul. Św. Wawrzyńca") == "Wawrzyńca"
    assert street_key_word("Marii Skłodowskiej-Curie") == "Skłodowskiej-Curie"
    assert street_key_word('rondo NSZZ "Solidarność"') == "Solidarność"


def test_street_key_word_nothing_meaningful():
    assert street_key_word("") is None
    assert street_key_word(None) is None
    assert street_key_word("ul. 3") is None
