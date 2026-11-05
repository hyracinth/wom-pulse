"""Print a weekly Wise Old Man group XP leaderboard."""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

WOM_API_BASE_URL = "https://api.wiseoldman.net/v2"
REQUEST_TIMEOUT_SECONDS = 30
MAX_HIGHLIGHTS = 5
USER_AGENT = "wom-pulse/1.0 (contact: 4340055+hyracinth@users.noreply.github.com)"
GEMINI_MODEL = "gemini-3.5-flash-lite"
SKILL_METRICS = frozenset(
    {
        "attack", "defence", "strength", "hitpoints", "ranged", "prayer",
        "magic", "cooking", "woodcutting", "fletching", "fishing",
        "firemaking", "crafting", "smithing", "mining", "herblore", "agility",
        "thieving", "slayer", "farming", "runecrafting", "hunter",
        "construction", "sailing",
    }
)
BOSS_METRICS = frozenset(
    {
        "abyssal_sire", "alchemical_hydra", "amoxliatl", "araxxor", "artio",
        "barrows_chests", "brutus", "bryophyta", "callisto", "calvarion",
        "cerberus", "chambers_of_xeric", "chambers_of_xeric_challenge_mode",
        "chaos_elemental", "chaos_fanatic", "commander_zilyana",
        "corporeal_beast", "crazy_archaeologist", "dagannoth_prime",
        "dagannoth_rex", "dagannoth_supreme", "deranged_archaeologist",
        "doom_of_mokhaiotl", "duke_sucellus", "general_graardor", "giant_mole",
        "grotesque_guardians", "hespori", "kalphite_queen",
        "king_black_dragon", "kraken", "kreearra", "kril_tsutsaroth",
        "lunar_chests", "mad_angel", "maggot_king", "mimic", "nex",
        "nightmare", "phosanis_nightmare", "obor", "phantom_muspah",
        "sarachnis", "scorpia", "scurrius", "shellbane_gryphon", "skotizo",
        "sol_heredit", "spindel", "tempoross", "the_gauntlet",
        "the_corrupted_gauntlet", "the_fractured_archive", "the_hueycoatl",
        "the_leviathan", "the_royal_titans", "the_whisperer",
        "theatre_of_blood", "theatre_of_blood_hard_mode",
        "thermonuclear_smoke_devil", "tombs_of_amascut",
        "tombs_of_amascut_expert", "tzkal_zuk", "tztok_jad", "vardorvis",
        "venenatis", "vetion", "vorkath", "wintertodt", "yama", "zalcano",
        "zulrah",
    }
)


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or Path(__file__).with_name(".env")
    if not env_path.exists():
        return

    for line_number, raw_line in enumerate(
        env_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid .env entry on line {line_number}")
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Invalid .env key on line {line_number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value


def fetch_json(url: str) -> Any:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    api_key = os.environ.get("WOM_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"WOM API returned HTTP {error.code}: {body}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach WOM API: {error.reason}") from error


def post_discord_message(webhook_url: str, message: str) -> None:
    if len(message) > 2000:
        raise ValueError(
            "Discord message exceeds the 2,000-character limit; shorten the report."
        )
    request = Request(
        webhook_url,
        data=json.dumps({"content": message}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            response.read()
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        if error.code == 403 and "1010" in body:
            raise RuntimeError(
                "Discord's edge protection blocked the webhook request "
                "(Cloudflare error 1010). The request now identifies wom-pulse "
                "with a User-Agent; verify the webhook URL and try the workflow "
                "again. If the block persists, create a new Discord webhook."
            ) from error
        raise RuntimeError(
            f"Discord webhook returned HTTP {error.code}: {body}"
        ) from error
    except URLError as error:
        raise RuntimeError(f"Could not reach Discord webhook: {error.reason}") from error


def build_commentary_summary(gains: list[dict[str, Any]]) -> str:
    xp_highlights = []
    for entry in top_xp_gains(gains):
        player = entry.get("player")
        name = (
            player.get("displayName") or player.get("username")
            if isinstance(player, dict)
            else "Unknown player"
        )
        skill = top_skill_gain(entry)
        xp_highlights.append(
            {
                "player": name,
                "overall_xp_gained": overall_xp_gained(entry),
                "top_skill": skill.get("metric") if skill else None,
                "top_skill_xp_gained": skill.get("gained") if skill else None,
            }
        )

    boss_highlights = []
    for item in top_boss_kc_gains(gains):
        player = item["player"]
        boss_highlights.append(
            {
                "player": player.get("displayName")
                or player.get("username")
                or "Unknown player",
                "boss": item["metric"],
                "kills_gained": item["gained"],
            }
        )

    return json.dumps(
        {"top_xp_gains": xp_highlights, "top_boss_kc_gains": boss_highlights},
        ensure_ascii=False,
    )


def generate_ai_commentary(clan_stats_summary: str, client: Any | None = None) -> str:
    if client is None:
        try:
            from google import genai
        except ImportError as error:
            raise RuntimeError(
                "Google GenAI SDK is not installed. Install dependencies with "
                "'py -3 -m pip install -r requirements.txt'."
            ) from error
        client = genai.Client()

    def create_interaction() -> Any:
        try:
            return client.interactions.create(
                model=GEMINI_MODEL,
                input=(
                    "Analyze the following OSRS clan weekly stats data:\n"
                    f"{clan_stats_summary}\n\n"
                    "Task: Write a short, quirky, and playful opening commentary "
                    "(at most 3-4 sentences) for this week's report.\n"
                    "Rules:\n"
                    "1. Ground every comment strictly in the provided data. If you "
                    "reference a player's name, it must make logical sense and "
                    "tie directly to their actual stats shown above.\n"
                    "2. Do not hallucinate or assume context outside of these numbers.\n"
                    "3. Keep it lighthearted, fun, encouraging, and casual. Avoid heavy "
                    "or mean-spirited roasting.\n"
                    "4. Whenever you mention a player, wrap their name in "
                    "Discord inline-code backticks, like `Player Name`.\n"
                    "5. Do not use standalone underscores, stray italics, or markdown "
                    "symbols outside of player backticks.\n"
                    "6. Return only the commentary text cleanly."
                ),
                generation_config={
                    "temperature": 1.0,
                    "thinking_level": "low",
                },
            )
        except Exception as error:
            if not isinstance(getattr(error, "status_code", None), int):
                raise
            raise RuntimeError(f"Gemini API request failed: {error}") from error

    response = create_interaction()

    commentary = response.output_text
    if not isinstance(commentary, str) or not commentary.strip():
        raise RuntimeError("Gemini returned an empty commentary response.")
    return commentary.strip()[:500]


def log_gemini_failure(error: RuntimeError) -> None:
    message = f"Gemini commentary unavailable; posting stats only: {error}"
    print(message, file=sys.stderr, flush=True)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(f"## Gemini commentary unavailable\n\n{message}\n")
    except OSError as summary_error:
        print(
            f"Could not write Gemini status to GitHub Actions summary: "
            f"{summary_error}",
            file=sys.stderr,
            flush=True,
        )


def load_group(group_id: str) -> dict[str, Any]:
    result = fetch_json(f"{WOM_API_BASE_URL}/groups/{group_id}")
    if not isinstance(result, dict):
        raise ValueError("WOM group response was not a JSON object")
    return result


def load_weekly_gains(group_id: str) -> list[dict[str, Any]]:
    result = fetch_json(f"{WOM_API_BASE_URL}/groups/{group_id}/bulk-gained?period=week")
    if not isinstance(result, list):
        raise ValueError("WOM weekly bulk gains response was not a JSON array")
    return [entry for entry in result if isinstance(entry, dict)]


def load_weekly_achievements(
    group_id: str, now: datetime | None = None
) -> list[dict[str, Any]]:
    result = fetch_json(f"{WOM_API_BASE_URL}/groups/{group_id}/achievements")
    if not isinstance(result, list):
        raise ValueError("WOM group achievements response was not a JSON array")

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        raise ValueError("The current time must include a timezone")
    cutoff = current_time - timedelta(days=7)
    recent_achievements = []

    for entry in result:
        if not isinstance(entry, dict):
            continue
        created_at = entry.get("createdAt")
        if not isinstance(created_at, str):
            raise ValueError("WOM achievement is missing a valid createdAt value")
        try:
            achievement_time = datetime.fromisoformat(
                created_at.replace("Z", "+00:00")
            )
        except ValueError as error:
            raise ValueError(
                f"WOM achievement has an invalid createdAt value: {created_at}"
            ) from error
        if achievement_time.tzinfo is None:
            achievement_time = achievement_time.replace(tzinfo=timezone.utc)
        if cutoff <= achievement_time <= current_time:
            recent_achievements.append(entry)

    return recent_achievements


def overall_xp_gained(entry: dict[str, Any]) -> int | float | None:
    data = entry.get("data")
    if isinstance(data, dict):
        gained = data.get("gained")
        return gained if isinstance(gained, (int, float)) else None
    if isinstance(data, list):
        for metric_data in data:
            if (
                isinstance(metric_data, dict)
                and metric_data.get("metric") == "overall"
                and isinstance(metric_data.get("gained"), (int, float))
            ):
                return metric_data["gained"]
    return None


def top_skill_gain(entry: dict[str, Any]) -> dict[str, Any] | None:
    data = entry.get("data")
    if not isinstance(data, list):
        return None
    skill_gains = [
        metric_data
        for metric_data in data
        if isinstance(metric_data, dict)
        and metric_data.get("metric") in SKILL_METRICS
        and isinstance(metric_data.get("gained"), (int, float))
        and metric_data["gained"] > 0
    ]
    return max(skill_gains, key=lambda metric_data: metric_data["gained"], default=None)


def ranked_gains(gains: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible_gains = [
        entry
        for entry in gains
        if (gained := overall_xp_gained(entry)) is not None and gained > 0
    ]
    return sorted(
        eligible_gains,
        key=lambda entry: overall_xp_gained(entry) or 0,
        reverse=True,
    )


def top_xp_gains(gains: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return ranked_gains(gains)[:MAX_HIGHLIGHTS]


def top_boss_kc_gains(gains: list[dict[str, Any]]) -> list[dict[str, Any]]:
    boss_gains = []
    for entry in gains:
        player = entry.get("player")
        data = entry.get("data")
        if not isinstance(player, dict) or not isinstance(data, list):
            continue
        for metric_data in data:
            if (
                isinstance(metric_data, dict)
                and metric_data.get("metric") in BOSS_METRICS
                and isinstance(metric_data.get("gained"), (int, float))
                and metric_data["gained"] > 0
            ):
                boss_gains.append(
                    {
                        "player": player,
                        "metric": metric_data["metric"],
                        "gained": metric_data["gained"],
                    }
                )
    return sorted(
        boss_gains,
        key=lambda item: item["gained"],
        reverse=True,
    )[:MAX_HIGHLIGHTS]


def format_xp(value: int) -> str:
    return f"{value:,}"


def build_report(
    group: dict[str, Any],
    gains: list[dict[str, Any]],
    commentary: str | None = None,
    report_date: date | None = None,
) -> str:
    group_name = group.get("name") or f"WOM group {group.get('id', '')}".strip()
    ranked = ranked_gains(gains)
    report_date = report_date or datetime.now(timezone.utc).date()

    lines = [
        f"**{group_name} — Weekly Pulse — "
        f"{report_date:%B} {report_date.day}, {report_date.year}**",
    ]
    if commentary:
        lines.extend(["", commentary])
    if not ranked:
        lines.extend(["", "**Top XP gains**", "No positive overall XP gains recorded."])
    else:
        lines.extend(["", "**Top XP gains**"])
        for rank, entry in enumerate(ranked[:MAX_HIGHLIGHTS], start=1):
            player = entry.get("player")
            display_name = (
                player.get("displayName") if isinstance(player, dict) else None
            ) or "Unknown player"
            skill_gain = top_skill_gain(entry)
            skill_highlight = ""
            if skill_gain:
                skill_name = str(skill_gain["metric"]).replace("_", " ").title()
                skill_highlight = (
                    f" *(Mainly {skill_name}: "
                    f"{format_xp(int(skill_gain['gained']))} XP)*"
                )
            lines.append(
                f"{rank}. **{display_name}** — "
                f"{format_xp(int(overall_xp_gained(entry) or 0))} XP"
                f"{skill_highlight}"
            )

    boss_gains = top_boss_kc_gains(gains)
    lines.extend(["", "**Top boss KC gains**"])
    if not boss_gains:
        lines.append("No positive boss KC gains recorded.")
    else:
        for rank, item in enumerate(boss_gains, start=1):
            player = item["player"]
            display_name = player.get("displayName") or "Unknown player"
            boss_name = str(item["metric"]).replace("_", " ").title()
            lines.append(
                f"{rank}. **{display_name}** — {boss_name}: "
                f"{format_xp(int(item['gained']))} KC"
            )

    return "\n".join(lines)


def main() -> int:
    try:
        load_dotenv()
    except (OSError, ValueError) as error:
        print(f"Could not load .env configuration: {error}", file=sys.stderr)
        return 1

    group_id = os.environ.get("WOM_GROUP_ID", "24255").strip()
    if not group_id:
        print("Missing required environment variable: WOM_GROUP_ID", file=sys.stderr)
        return 1
    if not group_id.isdecimal():
        print("WOM_GROUP_ID must be a numeric group ID.", file=sys.stderr)
        return 1

    try:
        group = load_group(group_id)
        gains = load_weekly_gains(group_id)
        commentary = None
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if api_key:
            try:
                commentary = generate_ai_commentary(build_commentary_summary(gains))
                print(
                    f"Gemini commentary generated successfully with {GEMINI_MODEL}.",
                    flush=True,
                )
            except RuntimeError as error:
                log_gemini_failure(error)
        else:
            print(
                "GEMINI_API_KEY is not set; posting stats without AI commentary."
            )
        report = build_report(group, gains, commentary=commentary)
        print(report)
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
        if webhook_url:
            post_discord_message(webhook_url, report)
            print("Posted weekly report to Discord.")
        else:
            print(
                "DISCORD_WEBHOOK_URL is not set; report was printed but not sent."
            )

    except (RuntimeError, ValueError) as error:
        print(f"Weekly WOM report failed: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
