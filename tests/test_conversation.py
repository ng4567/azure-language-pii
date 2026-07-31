import unittest

from benchmark import payload_characters
from conversation import Conversation, Turn


def _conversation():
    return Conversation(
        (
            Turn("Agent", "How can I help?"),
            Turn("Customer", "My statement is late."),
        )
    )


class ConversationTests(unittest.TestCase):
    def test_billable_characters_sums_every_turn(self):
        conv = _conversation()

        self.assertEqual(
            conv.billable_characters(),
            len("How can I help?") + len("My statement is late."),
        )

    def test_billable_characters_counts_utf16_code_units(self):
        conv = Conversation((Turn("Customer", "\U0001f600"),))

        # One astral code point is two UTF-16 code units, which is how the
        # conversation API enforces its per-turn limit.
        self.assertEqual(conv.billable_characters(), 2)

    def test_conversation_items_carry_stable_ids_and_roles(self):
        items = _conversation().to_conversation_items()

        self.assertEqual(
            items,
            [
                {
                    "id": "1",
                    "participantId": "Agent",
                    "role": "Agent",
                    "text": "How can I help?",
                },
                {
                    "id": "2",
                    "participantId": "Customer",
                    "role": "Customer",
                    "text": "My statement is late.",
                },
            ],
        )

    def test_as_text_renders_one_line_per_turn(self):
        self.assertEqual(
            _conversation().as_text(),
            "Agent: How can I help?\nCustomer: My statement is late.",
        )

    def test_dict_round_trip(self):
        conv = _conversation()

        self.assertEqual(Conversation.from_dicts(conv.to_dict()), conv)

    def test_payload_characters_uses_the_conversation_counter(self):
        conv = _conversation()

        self.assertEqual(payload_characters(conv), conv.billable_characters())

    def test_payload_characters_falls_back_to_string_counting(self):
        self.assertEqual(payload_characters("abc"), 3)


if __name__ == "__main__":
    unittest.main()
