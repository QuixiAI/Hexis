"""The reply language follows the message, not the context around it.

After one Japanese turn, or with Japanese memories recalled, an English
question used to get a Japanese answer.
"""

from services.agent import _reply_language_addendum, _reply_language_note, _with_reply_language


def test_a_scripted_message_asks_for_its_script():
    assert "Japanese: the user wrote" in _reply_language_note("北斗運輸の件はどうなりましたか？")
    assert "Korean" in _reply_language_note("오늘 회의는 어떻게 됐어요?")
    assert "Cyrillic" in _reply_language_note("Как прошла встреча сегодня?")


def test_a_latin_script_message_asks_for_its_own_language():
    note = _reply_language_note("Thanks. Summarize the three deals in one line each.")
    assert "The language the user wrote this message in" in note


def test_an_english_question_about_japanese_names_is_still_english():
    # Kanji without kana is a name here, not a Japanese sentence.
    assert "The language the user wrote" in _reply_language_note("What did 谷 say about the Hokuto renewal?")


def test_names_and_numbers_leave_the_working_language():
    assert _reply_language_note("Acme 2026") is None
    assert _reply_language_note("北斗運輸") is None
    assert _with_reply_language("Acme 2026") == "Acme 2026"  # no signal, no note


def test_the_note_follows_the_message():
    turn = _with_reply_language("Who is Ito?")
    assert turn.startswith("Who is Ito?\n\n[REPLY LANGUAGE]")


def test_system_prompt_states_the_language_through_tool_rounds():
    [section] = _reply_language_addendum("Give me a one-paragraph coaching note for Dan.")
    assert "between tool calls" in section and "latest message" in section
    assert "Japanese" in _reply_language_addendum("谷さんにアドバイスはありますか？")[0]
    assert _reply_language_addendum("Acme 2026") == []
