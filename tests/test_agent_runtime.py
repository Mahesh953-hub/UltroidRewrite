import asyncio
import unittest

from pyUltroid.agent._runtime import AgentRequest, AgentRuntime, resolve_caps


class FakeAgent:
    def __init__(self):
        self.calls = []

    async def respond(self, **kwargs):
        self.calls.append(kwargs)
        return "ok"


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_non_privileged_caps_exclude_shell(self):
        caps = resolve_caps(surface=".ai", user_id=999999, tools_enabled=True)
        self.assertNotIn("shell_exec", caps)
        self.assertNotIn("fs_write", caps)

    def test_disabled_tools_grant_no_caps(self):
        self.assertEqual(
            resolve_caps(surface=".ai", user_id=1, tools_enabled=False),
            frozenset(),
        )

    async def test_runtime_passes_normalized_request_to_agent(self):
        fake = FakeAgent()
        rt = AgentRuntime(fake)
        response = await rt.runtime_run(
            AgentRequest(
                prompt="hello",
                chat_id=42,
                origin=".ai",
                user_id=1,
                provider="openai",
                model="gpt-test",
                temperature=0.1,
                use_tools=False,
            )
        )
        self.assertEqual(response, "ok")
        call = fake.calls[0]
        self.assertEqual(call["user_message"], "hello")
        self.assertEqual(call["chat_id"], 42)
        self.assertEqual(call["provider_name"], "openai")
        self.assertEqual(call["model"], "gpt-test")
        self.assertEqual(call["temperature"], 0.1)
        self.assertFalse(call["use_tools"])
        self.assertEqual(call["scope"], ".ai")

    async def test_runtime_applies_surface_caps(self):
        fake = FakeAgent()
        rt = AgentRuntime(fake)
        await rt.runtime_run(
            AgentRequest(
                prompt="hello",
                chat_id=43,
                origin=".ai",
                user_id=999999,
                use_tools=True,
            )
        )
        caps = fake.calls[0]["caps"]
        self.assertNotIn("shell_exec", caps)

    async def test_runtime_timeout_cancels_hung_agent_and_returns_timeout(self):
        class HangingAgent:
            def __init__(self):
                self.started = asyncio.Event()
                self.cancelled = False

            async def respond(self, **kwargs):
                self.started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

        fake = HangingAgent()
        rt = AgentRuntime(fake)
        response = await rt.runtime_run(
            AgentRequest(
                prompt="hang safely",
                chat_id=44,
                origin=".agent",
                use_tools=False,
                timeout_seconds=0.01,
            )
        )
        self.assertTrue(fake.cancelled)
        self.assertFalse(response.ok)
        self.assertEqual(response.provider, "runtime")
        self.assertEqual(response.model, "timeout")
        self.assertIn("time limit", response.content)

    async def test_external_cancellation_is_not_misreported_as_timeout(self):
        class HangingAgent:
            def __init__(self):
                self.started = asyncio.Event()
                self.cancelled = False

            async def respond(self, **kwargs):
                self.started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

        fake = HangingAgent()
        rt = AgentRuntime(fake)
        task = asyncio.create_task(
            rt.runtime_run(
                AgentRequest(
                    prompt="cancel safely",
                    chat_id=45,
                    origin=".agent",
                    use_tools=False,
                    timeout_seconds=30,
                )
            )
        )
        await fake.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(fake.cancelled)


if __name__ == "__main__":
    unittest.main()
