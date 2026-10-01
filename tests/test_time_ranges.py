"""Tests for multi-range parsing used by audio trimming and pre-download ranges."""

import pytest

from bot.handlers.time_range import (
    RangeSpec,
    ResolvedRange,
    TimeRangeError,
    format_timestamp,
    looks_like_time_ranges,
    parse_time_ranges,
    range_to_session_dict,
    resolve_ranges,
)

DURATION = 6130  # 1:42:10


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1:30-4:45", [RangeSpec(90, 285)]),
        ("90-285", [RangeSpec(90, 285)]),
        ("1:02:30-1:05:00", [RangeSpec(3750, 3900)]),
        ("102:30-103:00", [RangeSpec(6150, 6180)]),
        ("2:15-", [RangeSpec(135, None)]),
        ("-5:00", [RangeSpec(None, 300)]),
        ("1:30 - 4:45", [RangeSpec(90, 285)]),
        ("1:30–4:45", [RangeSpec(90, 285)]),
        ("1:30—4:45", [RangeSpec(90, 285)]),
        ("1:00-2:00, 5:30-7:00", [RangeSpec(60, 120), RangeSpec(330, 420)]),
        ("1:00-2:00;5:30-7:00", [RangeSpec(60, 120), RangeSpec(330, 420)]),
        ("1:00-2:00\n5:30-", [RangeSpec(60, 120), RangeSpec(330, None)]),
        ("1:00-2:00,", [RangeSpec(60, 120)]),
        ("  1:00 -2:00 ;; 3:00-  ", [RangeSpec(60, 120), RangeSpec(180, None)]),
        ("1:00-2:00, 1:30-2:30", [RangeSpec(60, 120), RangeSpec(90, 150)]),
    ],
)
def test_parse_time_ranges_accepts_supported_forms(text, expected):
    assert parse_time_ranges(text) == expected


@pytest.mark.parametrize(
    "text, message_part",
    [
        ("abc", 'Nie rozumiem zakresu „abc"'),
        ("1:30", 'Nie rozumiem zakresu „1:30"'),
        ("1-2-3", 'Nie rozumiem zakresu „1-2-3"'),
        ("-", 'Zakres „-" musi mieć początek albo koniec.'),
        ("1:75-2:00", '„1:75" nie jest poprawnym czasem'),
        ("1:61:00-2:00:00", '„1:61:00" nie jest poprawnym czasem'),
        ("5:00-2:00", "W zakresie 5:00-2:00 początek musi być wcześniej niż koniec."),
        ("5:00-5:00", "W zakresie 5:00-5:00 początek musi być wcześniej niż koniec."),
        ("", "Podaj zakres."),
    ],
)
def test_parse_time_ranges_rejects_invalid_input(text, message_part):
    with pytest.raises(TimeRangeError) as exc_info:
        parse_time_ranges(text)
    assert message_part in str(exc_info.value)


def test_unparseable_message_lists_examples():
    with pytest.raises(TimeRangeError) as exc_info:
        parse_time_ranges("abc")
    assert "1:30-4:45 · 2:15- · -5:00 · 1:00-2:00, 5:30-7:00" in str(exc_info.value)


def test_parse_time_ranges_enforces_max_ranges():
    text = ", ".join(f"{i}-{i + 1}" for i in range(11))
    with pytest.raises(TimeRangeError) as exc_info:
        parse_time_ranges(text)
    assert str(exc_info.value) == "Możesz podać maksymalnie 10 fragmentów naraz (podano 11)."


@pytest.mark.parametrize(
    "specs, expected",
    [
        ([RangeSpec(90, 285)], [ResolvedRange(90, 285, False)]),
        ([RangeSpec(135, None)], [ResolvedRange(135, DURATION, True)]),
        ([RangeSpec(None, 300)], [ResolvedRange(0, 300, False)]),
        ([RangeSpec(0, DURATION - 1)], [ResolvedRange(0, DURATION - 1, False)]),
        ([RangeSpec(60, DURATION)], [ResolvedRange(60, DURATION, False)]),
    ],
)
def test_resolve_ranges_against_duration(specs, expected):
    assert resolve_ranges(specs, DURATION) == expected


@pytest.mark.parametrize(
    "specs, message",
    [
        ([RangeSpec(6200, None)], "Początek 1:43:20 jest poza plikiem (długość 1:42:10)."),
        (
            [RangeSpec(6000, 6300)],
            'Koniec 1:45:00 jest poza plikiem (długość 1:42:10). '
            'Wpisz „1:40:00-", żeby ciąć do końca.',
        ),
        ([RangeSpec(0, DURATION)], 'Zakres 0:00-1:42:10 obejmuje cały plik — nie ma czego ciąć.'),
        ([RangeSpec(0, None)], 'Zakres 0:00-1:42:10 obejmuje cały plik — nie ma czego ciąć.'),
        (
            [RangeSpec(10, 20), RangeSpec(6000, 6300)],
            'Koniec 1:45:00 jest poza plikiem (długość 1:42:10). '
            'Wpisz „1:40:00-", żeby ciąć do końca.',
        ),
    ],
)
def test_resolve_ranges_rejects_out_of_bounds(specs, message):
    with pytest.raises(TimeRangeError) as exc_info:
        resolve_ranges(specs, DURATION)
    assert str(exc_info.value) == message


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1:30-4:45", True),
        ("2:15-", True),
        ("-5:00", True),
        ("1:00-2:00, 5:30-7:00", True),
        ("1:30–4:45", True),
        ("12345678", False),
        ("https://youtube.com/watch?v=x", False),
        ("hello", False),
        ("-", False),
        ("", False),
    ],
)
def test_looks_like_time_ranges(text, expected):
    assert looks_like_time_ranges(text) is expected


def test_format_timestamp():
    assert format_timestamp(0) == "0:00"
    assert format_timestamp(285) == "4:45"
    assert format_timestamp(3599) == "59:59"
    assert format_timestamp(3600) == "1:00:00"
    assert format_timestamp(6130) == "1:42:10"


def test_range_to_session_dict_matches_legacy_shape():
    assert range_to_session_dict(10, 20) == {
        "start": "0:10",
        "end": "0:20",
        "start_sec": 10,
        "end_sec": 20,
    }
