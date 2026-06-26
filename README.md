# PracticePanther MCP

**Model Context Protocol (MCP) server for [PracticePanther](https://www.practicepanther.com/)** — cloud-based legal practice management software for solo and small-to-mid law firms.

Talk to PracticePanther from Claude, Cursor, or any MCP client. Read matters (cases), client accounts, contacts, time entries (billable hours), invoices, activities, tasks, calendar events — and create new records. Built against PracticePanther's public OAuth 2 + REST + OData API.

**No existing MCP for PracticePanther — this is the first.**

## What you can do with it

```
You:   "List open matters for account 'Acme Corp' and show last week's time entries."
Claude: *find_accounts → find_matters (odata filter status=open) → find_time_entries*

You:   "Log 2.5 hours on matter 4421 for Sarah Lee today — research on the deposition brief."
Claude: *create_time_entry with matterId=4421, userId=<Sarah's>, date=today, hours=2.5,
        description="Research: deposition brief", billable=true*

You:   "Show all unpaid invoices over 60 days old across the firm."
Claude: *find_invoices (status=sent/overdue, startDate=2024-04-01) → summary*

You:   "Book a hearing for matter 7788 on Friday May 30 at 2pm — judge Miller, courthouse 5."
Claude: *create_event with matterId=7788, startsAt, attendees, location*
```

## Why this exists

- PracticePanther's web UI is fine for solo use but doesn't expose firm-wide rollups (which matters are bleeding hours, which clients are slow-paying, who's about to blow a statute).
- Logging billable hours from the UI requires click-through per entry — Claude can batch-log 10 entries in a single conversation.
- The legal vertical has had [Clio MCP servers](https://github.com/punkpeye/awesome-mcp-servers) for over a year; solo and small firms on PracticePanther had nothing equivalent.
- For solo/small-firm attorneys, automating "what did I bill this week?" / "what's overdue?" / "log these 6 hours" is the most concrete, recurring time-save.

## Install

```bash
pip install -e .
```

## Authenticate (one-time)

PracticePanther uses OAuth 2 authorization-code flow. The included helper script runs the full flow locally and prints the env vars to set:

```bash
practicepanther-mcp-auth --client-id <your_client_id> --client-secret <your_client_secret>
```

This opens your browser, walks you through PracticePanther login, captures the redirect on `http://localhost:8765/callback`, exchanges the code for tokens, and prints:

```
export PRACTICEPANTHER_ACCESS_TOKEN="..."
export PRACTICEPANTHER_REFRESH_TOKEN="..."
export PRACTICEPANTHER_CLIENT_ID="..."
export PRACTICEPANTHER_CLIENT_SECRET="..."
```

To register an OAuth app, contact PracticePanther support or your account manager — they issue `client_id` + `client_secret` per integration.

## Configure

Once you have tokens, set them in your shell (or paste into the Claude Desktop config below):

```bash
export PRACTICEPANTHER_ACCESS_TOKEN="..."
export PRACTICEPANTHER_REFRESH_TOKEN="..."
export PRACTICEPANTHER_CLIENT_ID="..."
export PRACTICEPANTHER_CLIENT_SECRET="..."
```

Refresh tokens last 60 days. The MCP client auto-refreshes the access token; when the refresh token expires, just re-run `practicepanther-mcp-auth`.

## Use with Claude Desktop

```json
{
  "mcpServers": {
    "practicepanther_mcp": {
      "command": "practicepanther_mcp",
      "env": {
        "PRACTICEPANTHER_ACCESS_TOKEN": "...",
        "PRACTICEPANTHER_REFRESH_TOKEN": "...",
        "PRACTICEPANTHER_CLIENT_ID": "...",
        "PRACTICEPANTHER_CLIENT_SECRET": "..."
      }
    }
  }
}
```

## Tools (25)

| Tool | Type | What it does |
| --- | --- | --- |
| `health_check` | Diagnostic | Verifies credentials by listing users |
| `find_matters` | Read | Search matters (cases) — supports OData `$filter`, `$orderby`, `$select` |
| `get_matter` | Read | Single matter with full detail |
| `create_matter` | Write | Open new matter |
| `update_matter` | Write | Patch matter fields |
| `find_accounts` | Read | Search client accounts |
| `get_account` | Read | Single client account |
| `create_account` | Write | New client account |
| `find_contacts` | Read | List contacts on accounts |
| `create_contact` | Write | New contact |
| `find_time_entries` | Read | **The killer feature** — billable hours across the firm |
| `get_time_entry` | Read | Single time entry |
| `create_time_entry` | Write | Log billable/non-billable time |
| `update_time_entry` | Write | Patch time entry |
| `find_invoices` | Read | List invoices (filter by status, matter, account, date) |
| `get_invoice` | Read | Single invoice with line items |
| `create_invoice` | Write | New invoice |
| `find_activities` | Read | Calls, emails, meetings |
| `create_activity` | Write | Log activity against matter |
| `find_tasks` | Read | Tasks (to-dos) |
| `create_task` | Write | New task |
| `find_events` | Read | Calendar events (hearings, deadlines) |
| `create_event` | Write | New calendar event |
| `list_users` | Reference | Firm staff (attorneys, paralegals) |
| `list_practice_areas` | Reference | Practice areas (Family Law, PI, etc.) |
| `list_expense_categories` | Reference | Expense categories |

## API coverage

Maps MCP tools to PracticePanther's OData-enabled REST API (`/api/v2/*`). The full surface is exposed via Swagger UI: <https://app.practicepanther.com/swagger/ui/index>.

This MCP exposes the resources a solo/small-firm attorney most often needs: matters, accounts, contacts, time entries, invoices, activities, tasks, calendar. Document management and trust accounting endpoints are intentionally out of scope for v0.1 — open an issue if you need them.

## Rate limits

PracticePanther throttles at the OAuth-app level. The client auto-retries once on 401 (refreshes the access token) but does not retry on 429 — slow down if you hit rate limits.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src tests
practicepanther_mcp
```

## See also

- [PracticePanther API docs](https://support.practicepanther.com/en/articles/479897-practicepanther-api)
- [PracticePanther Swagger UI](https://app.practicepanther.com/swagger/ui/index)
- [Model Context Protocol](https://modelcontextprotocol.io)
- [More vertical MCPs from sanjibani](https://github.com/sanjibani?q=-mcp)

---

## Need a custom MCP for your SaaS?

I build production-grade MCP servers for vertical SaaS — insurance, dental, veterinary, legal, property mgmt, home services. Typical engagement: 2-4 weeks, $25K-$120K. Source-owned, MIT-licensed, no vendor lock-in.

See [sanjibani/mcp-services](https://github.com/sanjibani/mcp-services) or email schoudhury1991@gmail.com.

---

*Ships in the [sanjibani vertical-MCP portfolio](https://github.com/sanjibani?q=-mcp) — see also [hawksoft-mcp](https://github.com/sanjibani/hawksoft-mcp), [open-dental-mcp](https://github.com/sanjibani/open-dental-mcp), [ezyvet-mcp](https://github.com/sanjibani/ezyvet-mcp), [jobber-mcp](https://github.com/sanjibani/jobber-mcp), [mcp-vertical-template](https://github.com/sanjibani/mcp-vertical-template).*

---

MIT.

## Acknowledgements

- PracticePanther for the public REST + OData API + OAuth flow
- Built using [mcp-vertical-template](https://github.com/sanjibani/mcp-vertical-template)
- Inspired by [sanjibani/hawksoft-mcp](https://github.com/sanjibani/hawksoft-mcp) and the rest of the sanjibani vertical-MCP portfolio
