# PracticePanther MCP

**MCP server for PracticePanther MCP** — talk to your data from Claude, Cursor, or any MCP client.

## What you can do with it

```
You:   "Find every record updated this week and group them by status."
Claude: *calls the appropriate MCP tools, summarises the result*

You:   "Create a new record with these fields..."
Claude: *calls the create tool, confirms the result*
```

## Install

```bash
pip install -e .
```

## Configure

```bash
export PRACTICEPANTHER_USERNAME="..."
export PRACTICEPANTHER_PASSWORD="..."
```

### Who uses this?

1. **API Partners** building tools on top of PracticePanther MCP.
2. **Power users / agencies** doing their own custom integrations.

If you don't have credentials yet, contact PracticePanther MCP support to get set up.

## Use with Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "practicepanther_mcp": {
      "command": "practicepanther_mcp",
      "env": {
        "PRACTICEPANTHER_USERNAME": "your-username",
        "PRACTICEPANTHER_PASSWORD": "your-password"
      }
    }
  }
}
```

## Use with Claude Code

```bash
claude mcp add practicepanther_mcp -- practicepanther_mcp \
  --env PRACTICEPANTHER_USERNAME=your-user --env PRACTICEPANTHER_PASSWORD=your-pass
```

## Tools

| Tool | Type | What it does |
| --- | --- | --- |
| `health_check` | Diagnostic | Verifies credentials by hitting a known endpoint |

(TODO: list your actual tools here once defined)

## Development

```bash
pip install -e ".[dev]"
pytest
practicepanther_mcp
```

## License

MIT.

## See also

- [Model Context Protocol spec](https://modelcontextprotocol.io)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers)