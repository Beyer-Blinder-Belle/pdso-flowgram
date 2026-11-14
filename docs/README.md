# PDSO documentation

| Doc | For |
| --- | --- |
| [User Guide](user-guide.md) | People setting up and using PDSO on a project: concepts, install, project setup, everyday workflow, troubleshooting |
| [Parameter Reference](parameter-reference.md) | Every Data Silo parameter: type, who writes it, examples, and mapping from the original intranet names |
| [Standards README](../PDSO.extension/lib/pdso/standards/README.md) | Customizing what Model Setup creates (JSON standards) |
| [AGENTS.md](../AGENTS.md) | Coding agents: quick orientation, constraints, code map, known traps |
| [Agent: Architecture](agent/architecture.md) | Sync pipeline, storage schemas, correlation, known gaps |
| [Agent: Operating PDSO via MCP](agent/pyrevit-mcp.md) | Agent playbook for the PDSO MCP server, plus low-level scripting through the pyRevit agent runtime |

The agent docs are written to be dense and skimmable. Keep them in step with the code: when you change a parameter, schema, sync phase or known gap, update `AGENTS.md` and `agent/architecture.md` in the same pull request.
