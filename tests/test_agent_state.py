import unittest

from pyUltroid.agent._agent import AIMessage, ConversationHistory, UltroidAgent


class ChatSystemTests(unittest.TestCase):
    def test_chat_system_is_scoped(self):
        agent = UltroidAgent()
        agent.set_chat_system(1, "only one")
        agent.set_chat_system(2, "only two")
        self.assertEqual(agent.chat_system(1), "only one")
        self.assertEqual(agent.chat_system(2), "only two")
        agent.reset_chat_system(1)
        self.assertEqual(agent.chat_system(1), "")

    def test_legacy_constructed_agent_has_no_chat_prompt(self):
        agent = UltroidAgent.__new__(UltroidAgent)
        self.assertEqual(agent.chat_system(1), "")


class HistoryTests(unittest.TestCase):
    def test_tool_metadata_survives_a_persistence_round_trip(self):
        history = ConversationHistory(1)
        original = AIMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "c1", "name": "web_search", "arguments": {}}],
        )
        history.add_message(original)
        payload = history._local[0]
        self.assertEqual(payload.tool_calls[0]["id"], "c1")
        self.assertEqual(payload.tool_calls[0]["name"], "web_search")

    def test_add_still_creates_plain_messages(self):
        history = ConversationHistory(2)
        history.add("user", "hello")
        self.assertEqual(history._local[0].role, "user")
        self.assertEqual(history._local[0].content, "hello")


if __name__ == "__main__":
    unittest.main()
