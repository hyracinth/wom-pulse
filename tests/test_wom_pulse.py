import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import wom_pulse


class BuildReportTests(unittest.TestCase):
    def test_load_dotenv_loads_values_without_overriding_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                'WOM_GROUP_ID=123\nDISCORD_WEBHOOK_URL="https://example.com/hook"\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"WOM_GROUP_ID": "456"}, clear=True):
                wom_pulse.load_dotenv(env_path)

                self.assertEqual(os.environ["WOM_GROUP_ID"], "456")
                self.assertEqual(
                    os.environ["DISCORD_WEBHOOK_URL"], "https://example.com/hook"
                )

    @patch("wom_pulse.urlopen")
    def test_fetch_json_sends_wom_api_key_and_contact_user_agent(self, urlopen) -> None:
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = b'{"ok": true}'

        with patch.dict(os.environ, {"WOM_API_KEY": "test-wom-key"}):
            result = wom_pulse.fetch_json("https://example.com/data")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("X-api-key"), "test-wom-key")
        self.assertEqual(request.get_header("User-agent"), wom_pulse.USER_AGENT)
        self.assertEqual(result, {"ok": True})

    @patch("wom_pulse.urlopen")
    def test_post_discord_message_sends_webhook_payload(self, urlopen) -> None:
        response = urlopen.return_value.__enter__.return_value

        wom_pulse.post_discord_message("https://discord.example/webhook", "Weekly report")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("User-agent"), wom_pulse.USER_AGENT)
        self.assertEqual(
            json.loads(request.data),
            {"content": "Weekly report"},
        )

    def test_build_commentary_summary_uses_weekly_highlights(self) -> None:
        gains = [
            {
                "player": {"displayName": "Skill Grinder"},
                "data": [
                    {"metric": "overall", "gained": 5000},
                    {"metric": "agility", "gained": 900},
                    {"metric": "vorkath", "gained": 7},
                ],
            }
        ]

        summary = json.loads(wom_pulse.build_commentary_summary(gains))

        self.assertEqual(
            summary["top_xp_gains"][0],
            {
                "player": "Skill Grinder",
                "overall_xp_gained": 5000,
                "top_skill": "agility",
                "top_skill_xp_gained": 900,
            },
        )
        self.assertEqual(
            summary["top_boss_kc_gains"][0],
            {"player": "Skill Grinder", "boss": "vorkath", "kills_gained": 7},
        )

    def test_generate_ai_commentary_uses_gemini_model_and_prompt(self) -> None:
        from unittest.mock import Mock

        client = Mock()
        client.interactions.create.return_value.output_text = "Nice grind, clan."

        commentary = wom_pulse.generate_ai_commentary(
            '{"top_xp_gains": []}', client=client
        )

        self.assertEqual(commentary, "Nice grind, clan.")
        call = client.interactions.create.call_args
        self.assertEqual(call.kwargs["model"], wom_pulse.GEMINI_MODEL)
        self.assertIn('{"top_xp_gains": []}', call.kwargs["input"])
        self.assertIn("Ground every comment strictly in the provided data", call.kwargs["input"])
        self.assertIn(
            "wrap their name in Discord inline-code backticks, like `Player Name`",
            call.kwargs["input"],
        )
        self.assertIn(
            "Do not use standalone underscores, stray italics, or markdown "
            "symbols outside of player backticks.",
            call.kwargs["input"],
        )
        self.assertIn("Return only the commentary text cleanly.", call.kwargs["input"])
        self.assertEqual(call.kwargs["generation_config"]["temperature"], 1.0)
        self.assertEqual(call.kwargs["generation_config"]["thinking_level"], "low")

    def test_generate_ai_commentary_wraps_gemini_http_errors(self) -> None:
        from unittest.mock import Mock

        client = Mock()
        error = RuntimeError("service unavailable")
        error.status_code = 503
        client.interactions.create.side_effect = error

        with self.assertRaisesRegex(RuntimeError, "Gemini API request failed"):
            wom_pulse.generate_ai_commentary("{}", client=client)

    def test_log_gemini_failure_writes_actions_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary_path = Path(directory) / "summary.md"
            stderr = StringIO()
            with patch.dict(
                os.environ, {"GITHUB_STEP_SUMMARY": str(summary_path)}, clear=True
            ), redirect_stderr(stderr):
                wom_pulse.log_gemini_failure(
                    RuntimeError("Gemini API request failed: HTTP 503")
                )

            self.assertIn("HTTP 503", stderr.getvalue())
            self.assertIn(
                "Gemini commentary unavailable",
                summary_path.read_text(encoding="utf-8"),
            )

    def test_build_report_sorts_and_limits_xp_highlights(self) -> None:
        gains = [
            {"player": {"displayName": "Second"}, "data": {"gained": 250}},
            {
                "player": {"displayName": "Top Display", "username": "Top"},
                "data": {"gained": 500},
            },
            {"player": {"username": "No gain"}, "data": {"gained": 0}},
        ] + [
            {
                "player": {"username": f"Player {number}"},
                "data": {"gained": 100 - number},
            }
            for number in range(6)
        ]

        report = wom_pulse.build_report({"name": "The Test Clan"}, gains)

        self.assertIn("**The Test Clan — Weekly Pulse — ", report)
        self.assertIn("**Top XP gains**", report)
        self.assertIn("1. **Top Display** — 500 XP", report)
        self.assertIn("2. **Second** — 250 XP", report)
        self.assertNotIn("(`Top`)", report)
        self.assertNotIn("No gain", report)
        self.assertIn("**Top boss KC gains**", report)
        self.assertIn("No positive boss KC gains recorded.", report)

    def test_build_report_orders_title_date_commentary_before_stats(self) -> None:
        report = wom_pulse.build_report(
            {"name": "Dry Forever"},
            [],
            commentary="The squid is winning this week.",
            report_date=datetime(2026, 11, 3, tzinfo=timezone.utc).date(),
        )

        self.assertEqual(
            report.splitlines()[:4],
            [
                "**Dry Forever — Weekly Pulse — November 3, 2026**",
                "",
                "The squid is winning this week.",
                "",
            ],
        )
        self.assertLess(report.index("The squid is winning this week."), report.index("**Top XP gains**"))

    def test_build_report_handles_no_positive_gains(self) -> None:
        report = wom_pulse.build_report(
            {"name": "The Test Clan"},
            [{"player": {"username": "Idle"}, "data": {"gained": 0}}],
        )

        self.assertIn("No positive overall XP gains", report)

    @patch("wom_pulse.fetch_json")
    def test_load_weekly_gains_requests_bulk_week_metrics(self, fetch_json) -> None:
        fetch_json.return_value = [
            {"data": [{"metric": "overall", "gained": 1}]}
        ]

        result = wom_pulse.load_weekly_gains("123")

        self.assertEqual(
            result,
            [{"data": [{"metric": "overall", "gained": 1}]}],
        )
        fetch_json.assert_called_once_with(
            "https://api.wiseoldman.net/v2/groups/123/bulk-gained?period=week"
        )

    @patch("wom_pulse.fetch_json")
    def test_load_weekly_achievements_filters_to_last_seven_days(
        self, fetch_json
    ) -> None:
        fetch_json.return_value = [
            {"name": "Recent", "createdAt": "2026-11-02T12:00:00.000Z"},
            {"name": "Boundary", "createdAt": "2026-10-27T12:00:00+00:00"},
            {"name": "Old", "createdAt": "2026-10-27T11:59:59+00:00"},
        ]
        now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

        result = wom_pulse.load_weekly_achievements("123", now=now)

        self.assertEqual([entry["name"] for entry in result], ["Recent", "Boundary"])
        fetch_json.assert_called_once_with(
            "https://api.wiseoldman.net/v2/groups/123/achievements"
        )

    def test_top_xp_gains_returns_full_records_for_top_five(self) -> None:
        players = [
            {"id": number, "displayName": f"Player {number}"}
            for number in range(7)
        ]
        gains = [
            {"player": player, "data": {"gained": gained}, "details": number}
            for number, (player, gained) in enumerate(
                zip(players, [100, 500, 300, 200, 400, 0, 600])
            )
        ]

        self.assertEqual(
            wom_pulse.top_xp_gains(gains),
            [gains[6], gains[1], gains[4], gains[2], gains[3]],
        )

    def test_ranks_gain_records_with_metric_array_data(self) -> None:
        gains = [
            {
                "player": {"username": "Top"},
                "data": [
                    {"metric": "overall", "gained": 500},
                    {"metric": "agility", "gained": 200},
                ],
            },
            {
                "player": {"username": "Second"},
                "data": [{"metric": "overall", "gained": 300}],
            },
        ]

        self.assertEqual(wom_pulse.ranked_gains(gains), gains)

    def test_top_boss_kc_gains_ranks_positive_boss_kills(self) -> None:
        gains = [
            {
                "player": {"displayName": "First"},
                "data": [
                    {"metric": "vorkath", "gained": 12},
                    {"metric": "attack", "gained": 100000},
                    {"metric": "zulrah", "gained": 3},
                ],
            },
            {
                "player": {"displayName": "Second"},
                "data": [{"metric": "zulrah", "gained": 25}],
            },
            {
                "player": {"displayName": "Third"},
                "data": [{"metric": "abyssal_sire", "gained": 0}],
            },
        ]

        result = wom_pulse.top_boss_kc_gains(gains)

        self.assertEqual(
            [(item["player"]["displayName"], item["metric"], item["gained"])
             for item in result],
            [
                ("Second", "zulrah", 25),
                ("First", "vorkath", 12),
                ("First", "zulrah", 3),
            ],
        )

    def test_build_report_formats_discord_friendly_xp_and_boss_lists(self) -> None:
        gains = [
            {
                "player": {
                    "displayName": "Boss Hunter",
                    "username": "boss_hunter",
                },
                "data": [
                    {"metric": "overall", "gained": 250000},
                    {"metric": "agility", "gained": 12000},
                    {"metric": "attack", "gained": 8000},
                    {"metric": "vorkath", "gained": 18},
                ],
            }
        ]

        report = wom_pulse.build_report({"name": "The Test Clan"}, gains)

        self.assertIn("**Top XP gains**", report)
        self.assertIn(
            "**Boss Hunter** — 250,000 XP "
            "*(Mainly Agility: 12,000 XP)*",
            report,
        )
        self.assertIn("**Top boss KC gains**", report)
        self.assertIn("**Boss Hunter** — Vorkath: 18 KC", report)
        self.assertNotIn("boss_hunter", report)

    def test_top_skill_gain_ignores_non_skill_metrics_and_zero_gains(self) -> None:
        entry = {
            "data": [
                {"metric": "overall", "gained": 500000},
                {"metric": "zulrah", "gained": 100},
                {"metric": "attack", "gained": 0},
                {"metric": "agility", "gained": 321},
                {"metric": "magic", "gained": 987},
            ]
        }

        self.assertEqual(
            wom_pulse.top_skill_gain(entry),
            {"metric": "magic", "gained": 987},
        )

    @patch.dict(
        "os.environ",
        {
            "WOM_GROUP_ID": "123",
            "DISCORD_WEBHOOK_URL": "https://discord.example/hook",
            "GEMINI_API_KEY": "test-key",
        },
        clear=True,
    )
    @patch("wom_pulse.load_dotenv")
    @patch("wom_pulse.load_group", return_value={"name": "The Test Clan"})
    @patch(
        "wom_pulse.load_weekly_gains",
        return_value=[
            {
                "player": {"displayName": "Top Display", "username": "Top"},
                "data": {"gained": 500},
            }
        ],
    )
    @patch("wom_pulse.post_discord_message")
    @patch("wom_pulse.generate_ai_commentary", return_value="Nice grind, clan.")
    @patch("wom_pulse.build_commentary_summary", return_value='{"top_xp_gains":[]}')
    def test_main_prints_and_posts_report(
        self,
        build_commentary_summary,
        generate_ai_commentary,
        post_discord_message,
        load_weekly_gains,
        load_group,
        load_dotenv,
    ) -> None:
        output = StringIO()

        with redirect_stdout(output):
            result = wom_pulse.main()

        self.assertEqual(result, 0)
        output_text = output.getvalue()
        self.assertIn("**Top XP gains**", output_text)
        self.assertIn("1. **Top Display** — 500 XP", output_text)
        self.assertIn("Gemini commentary generated successfully", output_text)
        report_text = output_text[output_text.index("**The Test Clan") :]
        self.assertLess(report_text.index("**The Test Clan"), report_text.index("Nice grind, clan."))
        self.assertLess(
            report_text.index("Nice grind, clan."),
            report_text.index("**Top XP gains**"),
        )
        build_commentary_summary.assert_called_once()
        generate_ai_commentary.assert_called_once_with('{"top_xp_gains":[]}')
        post_discord_message.assert_called_once_with(
            "https://discord.example/hook",
            unittest.mock.ANY,
        )
        load_group.assert_called_once_with("123")
        load_weekly_gains.assert_called_once_with("123")
        load_dotenv.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
