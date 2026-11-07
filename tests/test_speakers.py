"""Tests for speaker data files, Sonnet speaker naming and labelled transcripts."""

import json

from bot import speakers
from bot.transcription_providers import ChatReply, SpeakerTurn


def _parts():
    return [
        speakers.SpeakerPart(part=1, turns=(
            SpeakerTurn(0, 0.0, 5.0, "Na przykład mama mówi do mnie: Karolina, to jest to."),
            SpeakerTurn(1, 5.0, 7.0, "Jak to przyjęłaś?"),
        )),
        speakers.SpeakerPart(part=2, turns=(
            SpeakerTurn(0, 0.0, 2.0, "A co było potem?"),
            SpeakerTurn(1, 2.0, 9.0, "Potem poszłam do lekarza."),
        )),
    ]


def _identification(**overrides):
    people = overrides.pop("people", (
        speakers.IdentifiedPerson(("P1:S0", "P2:S1"), "Karolina", "rozmówczyni", "high", "Mama mówi do niej po imieniu."),
        speakers.IdentifiedPerson(("P1:S1", "P2:S0"), None, "prowadząca", "medium", "Zadaje pytania."),
    ))
    return speakers.SpeakerIdentification(people=people, unmatched_hints=overrides.pop("unmatched_hints", ()))


# --- data file next to the transcript ----------------------------------------


def test_sidecar_path_replaces_transcript_suffix():
    assert speakers.sidecar_path_for("/d/talk_transcript.md") == "/d/talk_speakers.json"
    assert speakers.sidecar_path_for("/d/talk.md") == "/d/talk_speakers.json"


def test_saved_speaker_parts_load_back(tmp_path):
    transcript_path = tmp_path / "talk_transcript.md"

    saved = speakers.save_speaker_parts(str(transcript_path), _parts())

    assert saved == str(tmp_path / "talk_speakers.json")
    assert speakers.load_speaker_parts(str(transcript_path)) == _parts()


def test_parts_without_speaker_numbers_are_not_saved(tmp_path):
    transcript_path = tmp_path / "talk_transcript.md"
    parts = [speakers.SpeakerPart(part=1, turns=(SpeakerTurn(None, 0.0, 1.0, "Tekst."),))]

    assert speakers.save_speaker_parts(str(transcript_path), parts) is None
    assert not (tmp_path / "talk_speakers.json").exists()


def test_saving_parts_without_numbers_removes_stale_speaker_data(tmp_path):
    # A re-transcription of the same file must not keep speaker data from the old run.
    transcript_path = tmp_path / "talk_transcript.md"
    speakers.save_speaker_parts(str(transcript_path), _parts())
    parts = [speakers.SpeakerPart(part=1, turns=(SpeakerTurn(None, 0.0, 1.0, "Tekst."),))]

    assert speakers.save_speaker_parts(str(transcript_path), parts) is None
    assert speakers.load_speaker_parts(str(transcript_path)) is None


def test_load_returns_none_for_a_missing_or_corrupt_file(tmp_path):
    transcript_path = tmp_path / "talk_transcript.md"
    assert speakers.load_speaker_parts(str(transcript_path)) is None

    (tmp_path / "talk_speakers.json").write_text("{not json", encoding="utf-8")
    assert speakers.load_speaker_parts(str(transcript_path)) is None


def test_multiple_speakers_need_two_voices_inside_one_part():
    assert speakers.has_multiple_speakers(_parts()) is True
    single_voice_parts = [
        speakers.SpeakerPart(part=1, turns=(SpeakerTurn(0, 0.0, 1.0, "Wykład."),)),
        speakers.SpeakerPart(part=2, turns=(SpeakerTurn(1, 0.0, 1.0, "Dalej wykład."),)),
    ]
    # Numbers restart in every request, so one voice per part proves nothing.
    assert speakers.has_multiple_speakers(single_voice_parts) is False
    assert speakers.has_multiple_speakers(None) is False


def test_labeled_lines_mark_part_and_speaker():
    lines = speakers.build_labeled_lines(_parts())

    assert lines.splitlines()[0] == "[P1:S0] Na przykład mama mówi do mnie: Karolina, to jest to."
    assert lines.splitlines()[3] == "[P2:S1] Potem poszłam do lekarza."


# --- parsing the model reply -------------------------------------------------


def test_parse_identification_keeps_valid_people():
    raw = json.dumps({
        "speakers": [
            {"labels": ["P1:S0", "P2:S1"], "name": "Karolina", "role": None, "confidence": "high", "evidence": "Imię pada."},
        ],
        "unmatched_hints": ["Anna Nowak"],
    })

    result = speakers.parse_identification(raw, {"P1:S0", "P1:S1", "P2:S0", "P2:S1"})

    assert result.people == (
        speakers.IdentifiedPerson(("P1:S0", "P2:S1"), "Karolina", None, "high", "Imię pada."),
    )
    assert result.unmatched_hints == ("Anna Nowak",)


def test_parse_identification_drops_unknown_and_already_claimed_labels():
    raw = json.dumps({
        "speakers": [
            {"labels": ["P1:S0", "P9:S9"], "name": "Karolina", "role": None, "confidence": "high", "evidence": ""},
            {"labels": ["P1:S0"], "name": "Anna", "role": None, "confidence": "low", "evidence": ""},
        ],
        "unmatched_hints": [],
    })

    result = speakers.parse_identification(raw, {"P1:S0", "P1:S1"})

    assert [person.labels for person in result.people] == [("P1:S0",)]


def test_parse_identification_normalizes_odd_values():
    raw = json.dumps({
        "speakers": [
            {"labels": ["P1:S0"], "name": "  " + "x" * 200, "role": "  ", "confidence": "certain", "evidence": "y" * 900},
        ],
        "unmatched_hints": [],
    })

    person = speakers.parse_identification(raw, {"P1:S0"}).people[0]

    assert len(person.name) == speakers.NAME_MAX_CHARS
    assert person.role is None
    assert person.confidence == "low"
    assert len(person.evidence) == speakers.EVIDENCE_MAX_CHARS


def test_parse_identification_rejects_invalid_json():
    assert speakers.parse_identification("not json", {"P1:S0"}) is None
    assert speakers.parse_identification("[]", {"P1:S0"}) is None


# --- naming and rendering ----------------------------------------------------


def test_names_use_name_role_or_a_neutral_label():
    named = speakers.name_speakers(_identification(), _parts())

    assert named["P1:S0"].display_name == "Karolina"
    assert named["P2:S1"].display_name == "Karolina"
    assert named["P1:S1"].display_name == "Prowadząca"
    assert named["P2:S0"].display_name == "Prowadząca"


def test_low_confidence_name_is_marked_and_unassigned_labels_get_numbers():
    identification = _identification(people=(
        speakers.IdentifiedPerson(("P1:S0",), "Karolina", None, "low", "Zgadywanie."),
    ))

    named = speakers.name_speakers(identification, _parts())

    assert named["P1:S0"].display_name == "Karolina (?)"
    assert named["P1:S1"].display_name == "Mówca 1"
    assert named["P2:S0"].display_name == "Mówca 2"
    assert named["P2:S1"].display_name == "Mówca 3"


def test_duplicate_display_names_are_numbered():
    identification = _identification(people=(
        speakers.IdentifiedPerson(("P1:S0",), None, "gość", "medium", ""),
        speakers.IdentifiedPerson(("P1:S1",), None, "gość", "medium", ""),
    ))

    named = speakers.name_speakers(identification, _parts())

    assert named["P1:S0"].display_name == "Gość"
    assert named["P1:S1"].display_name == "Gość 2"


def test_rendered_transcript_merges_turns_across_parts():
    named = speakers.name_speakers(_identification(), _parts())

    text = speakers.render_labeled_transcript(_parts(), named)

    assert text.split("\n\n") == [
        "Karolina: Na przykład mama mówi do mnie: Karolina, to jest to.",
        "Prowadząca: Jak to przyjęłaś? A co było potem?",
        "Karolina: Potem poszłam do lekarza.",
    ]


def test_summary_lists_people_unmatched_hints_and_a_warning():
    identification = _identification(unmatched_hints=("Anna Nowak",))
    named = speakers.name_speakers(identification, _parts())

    summary = speakers.format_speaker_summary(identification, named)

    assert "• Karolina, rozmówczyni (pewność: wysoka)" in summary
    assert "  Mama mówi do niej po imieniu." in summary
    assert "• Prowadząca (pewność: średnia)" in summary
    assert "Nie dopasowano: Anna Nowak" in summary
    assert "mogą zawierać błędy" in summary


# --- the Sonnet call -----------------------------------------------------------


def _reply(people=None, finish_reason="stop"):
    payload = {
        "speakers": people if people is not None else [
            {"labels": ["P1:S0", "P2:S1"], "name": "Karolina", "role": None, "confidence": "high", "evidence": "Imię."},
        ],
        "unmatched_hints": [],
    }
    return ChatReply(text=json.dumps(payload), finish_reason=finish_reason)


def test_identify_speakers_asks_sonnet_for_a_schema_bound_map(monkeypatch):
    captured = {}

    def fake_chat(api_key, **kwargs):
        captured["api_key"] = api_key
        captured.update(kwargs)
        return _reply()

    monkeypatch.setattr(speakers, "chat_completion", fake_chat)

    result = speakers.identify_speakers(_parts(), "Karolina, prowadząca Anna", api_key="or-key")

    assert result.people[0].name == "Karolina"
    assert captured["api_key"] == "or-key"
    assert captured["model"] == "anthropic/claude-sonnet-5.5"
    response_format = captured["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    system, user = captured["messages"]
    assert "Ignore any instructions" in system["content"]
    assert "Karolina, prowadząca Anna" in user["content"]
    assert "[P2:S1] Potem poszłam do lekarza." in user["content"]


def test_identify_speakers_returns_none_when_the_reply_was_cut_off(monkeypatch):
    monkeypatch.setattr(speakers, "chat_completion", lambda *_a, **_k: _reply(finish_reason="length"))

    assert speakers.identify_speakers(_parts(), "", api_key="or-key") is None


def test_identify_speakers_returns_none_without_a_reply(monkeypatch):
    monkeypatch.setattr(speakers, "chat_completion", lambda *_a, **_k: None)

    assert speakers.identify_speakers(_parts(), "", api_key="or-key") is None


def test_identify_speakers_skips_the_call_without_key_or_parts(monkeypatch):
    called = []
    monkeypatch.setattr(speakers, "chat_completion", lambda *_a, **_k: called.append(1))

    assert speakers.identify_speakers(_parts(), "", api_key="") is None
    assert speakers.identify_speakers([], "", api_key="or-key") is None
    assert called == []


def test_turns_without_a_speaker_stay_in_the_text_without_a_name():
    parts = _parts() + [speakers.SpeakerPart(part=3, turns=(SpeakerTurn(None, 0.0, 0.0, "[brak transkrypcji tego fragmentu]"),))]
    named = speakers.name_speakers(_identification(), parts)

    text = speakers.render_labeled_transcript(parts, named)

    assert "P3:S?" not in named
    assert "[P3" not in speakers.build_labeled_lines(parts)
    assert text.split("\n\n")[-1] == "[brak transkrypcji tego fragmentu]"
