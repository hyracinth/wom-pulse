# wom-pulse

A small Python script that reads a Wise Old Man (WOM) group's weekly bulk gains
and prints a Discord-ready weekly summary with the top five overall XP earners
and top five player/boss kill-count gains. Each overall XP highlight also
includes the player's highest-gaining skill for the week. Optional Google
Gemini commentary adds a short, stats-based, lighthearted intro.
It uses Google's stable `gemini-3.5-flash-lite` model by default.

## Configure

Add these repository secrets under **Settings → Secrets and variables → Actions**:

- `WOM_GROUP_ID`: the numeric ID of your WOM group.
- `WOM_API_KEY`: a Wise Old Man API key (optional).
- `DISCORD_WEBHOOK_URL`: the Discord channel's webhook URL.
- `GEMINI_API_KEY`: an API key from Google AI Studio for commentary (optional).

If you have a WOM API key, save it as the `WOM_API_KEY` repository secret to
use the higher API rate limits in the scheduled workflow.

Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/apikey)
and add it as the `GEMINI_API_KEY` repository secret. Without this secret, the
workflow still posts the stats report, but skips AI commentary.

Create a Discord webhook in **Server Settings → Integrations → Webhooks**, copy
its URL, and save it directly as the `DISCORD_WEBHOOK_URL` secret. Do not commit
the webhook URL or put it in a tracked file. The workflow runs every 15 minutes
(UTC) and can also be started from **Actions → Weekly WOM report → Run
workflow**. It prints the report in the job log and posts it to Discord.

For local runs, create a `.env` file in the project folder:

```dotenv
WOM_GROUP_ID=24255
WOM_API_KEY=your-wise-old-man-api-key
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/your-webhook-url
GEMINI_API_KEY=your-google-ai-studio-api-key
```

Install the Python dependencies before running locally:

```powershell
py -3 -m pip install -r requirements.txt
py -3 wom_pulse.py
```

The script loads `.env` automatically, prints the report, and posts it when a
webhook URL is configured. When `GEMINI_API_KEY` is configured, it sends a
compact summary of the top XP and boss-KC highlights to Gemini, then prepends
the returned commentary to the report. If Gemini fails, the script reports the
error in the Actions log and run summary, then still posts the stats-only report.
When `WOM_API_KEY` is configured, it is sent in the `x-api-key` header for WOM
API requests.
`.env` is gitignored. Never upload the real `.env`; add values as GitHub
repository secrets instead. A safe placeholder template is provided in
`.env.example`.

The report uses Discord-compatible Markdown headings and numbered lists.
Discord does not render Markdown tables, so the leaderboards are formatted as
lists. The boss leaderboard ranks player/boss combinations by kills gained
during the week. Quirky LLM-written commentary can be added later.

## Test

```powershell
python -m unittest discover -s tests
```
