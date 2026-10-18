"""Regression coverage for user-facing agent delivery and private telemetry."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch


class AgentRunTelemetryTests(unittest.TestCase):
    def test_successful_run_report_is_omitted(self):
        from pyUltroid.agent import _reporting

        with patch.object(_reporting, "_log_channel") as channel:
            result = _reporting.report_run(
                origin=".agent",
                chat_id=123,
                user="alice",
                provider="mock",
                model="mock-model",
                prompt="private user prompt",
                ok=True,
            )
        self.assertIsNone(result)
        channel.assert_not_called()

    def test_failed_run_report_never_contains_user_prompt(self):
        from pyUltroid.agent import _reporting

        captured = []

        async def capture(header, lines):
            captured.extend(lines)

        async def run_report():
            with (
                patch.object(_reporting, "_log_channel", return_value=-100123),
                patch.object(_reporting, "_report", side_effect=capture),
            ):
                task = _reporting.report_run(
                    origin="dm-session",
                    chat_id=123,
                    user="alice",
                    provider="mock",
                    model="mock-model",
                    prompt="private user prompt",
                    ok=False,
                    error="ProviderError",
                )
                await task

        asyncio.run(run_report())
        self.assertNotIn("Prompt", [label for label, _ in captured])
        self.assertNotIn("private user prompt", repr(captured))


if __name__ == "__main__":
    unittest.main()
