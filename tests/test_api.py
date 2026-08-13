from manus_cli.api import last_assistant_message


def test_last_assistant_message_finds_first_match_in_desc_order():
    messages = [
        {"type": "assistant_message", "assistant_message": {"content": "resposta final"}},
        {"type": "user_message", "user_message": {"content": "pergunta"}},
    ]
    assert last_assistant_message(messages) == "resposta final"


def test_last_assistant_message_none_when_absent():
    assert last_assistant_message([{"type": "status_update"}]) is None


if __name__ == "__main__":
    test_last_assistant_message_finds_first_match_in_desc_order()
    test_last_assistant_message_none_when_absent()
    print("ok")
