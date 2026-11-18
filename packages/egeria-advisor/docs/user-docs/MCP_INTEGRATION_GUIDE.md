# MCP Integration Guide

**Version:** 1.1
**Date:** 2026-02-20
**Status: superseded snapshot.** This describes MCP integration as of Feb 2026 (Phase 10.1/10.2,
with 10.3 "pending"). MCP integration is now fully built into the main system — the Dr.Egeria
MCP server backs report execution (`ReportPipeline`), command execution (`DrEgeriaActionAgent`),
and plan execution (`GovernancePlanAgent`); see the Query Flow diagram in `CLAUDE.md` and
`docs/PROJECT_SUMMARY.md` Phases 10–14 for current architecture and status. The conceptual
"What is MCP?" background below is still accurate; the phase/status details above it are not.

## Overview

The Egeria Advisor now supports Model Context Protocol (MCP) integration, enabling it to invoke tools from MCP servers like the pyegeria MCP server for reports and Dr. Egeria markdown command construction/invocation.

## Current Status

✅ **Phase 10.1 Complete**: Core MCP implementation with standalone CLI
✅ **Phase 10.2 Complete**: RAG integration with tool-augmented queries
⏳ **Phase 10.3 Pending**: Integration into main agent CLI

**Available Now:**
- `examples/cli_with_mcp.py` - Interactive tool execution CLI
- `examples/cli_with_tools_and_rag.py` - Tool-augmented RAG queries
- `advisor/mcp_client.py` - MCP client library
- `advisor/mcp_agent.py` - Multi-server agent
- `advisor/tool_augmented_rag.py` - RAG with tool invocation

**Not Yet Available:**
- MCP tool execution in main agent CLI (`egeria-advisor --agent --interactive`)
- This will be added in Phase 10.3

## What is MCP?

Model Context Protocol (MCP) is a standardized protocol for connecting AI applications to external tools and data sources. It allows the Egeria Advisor to:

- **Invoke Tools**: Execute operations like generating reports or running commands
- **Access Resources**: Read data from external systems
- **Extend Capabilities**: Add new functionality without modifying core code

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Egeria Advisor                            │
│                                                              │
│  ┌────────────────┐      ┌──────────────────┐              │
│  │  RAG System    │      │   MCP Agent      │              │
│  │                │      │                  │              │
│  │  - Query       │◄────►│  - Tool Discovery│              │
│  │  - Routing     │      │  - Tool Invocation│             │
│  │  - Response    │      │  - Result Handling│             │
│  └────────────────┘      └──────────────────┘              │
│         │                         │                         │
│         │                         │                         │
│         ▼                         ▼                         │
│  ┌────────────────────────────────────────┐                │
│  │         LLM Client                      │                │
│  │  - Prompt Construction                  │                │
│  │  - Tool Call Detection                  │                │
│  │  - Response Generation                  │                │
│  └────────────────────────────────────────┘                │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ MCP Protocol (JSON-RPC)
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                  MCP Servers                                 │
│                                                              │
│  ┌──────────────────┐      ┌──────────────────┐            │
│  │  pyegeria MCP    │      │  Future MCP      │            │
│  │                  │      │  Servers         │            │
│  │  - Reports       │      │                  │            │
│  │  - Dr. Egeria    │      │  - File System   │            │
│  │  - Commands      │      │  - Database      │            │
│  └──────────────────┘      └──────────────────┘            │
└─────────────────────────────────────────────────────────────┘
```

## Setup

### 1. Configuration File

Create `config/mcp_servers.json` from the example:

```bash
cp config/mcp_servers.json.example config/mcp_servers.json
```

Edit the configuration to match your environment:

```json
{
  "mcpServers": {
    "pyegeria": {
      "command": "/path/to/python",
      "args": [
        "/path/to/pyegeria/core/mcp_server.py"
      ],
      "env": {
        "EGERIA_VIEW_SERVER": "your_server",
        "EGERIA_VIEW_SERVER_URL": "https://your-server:9443"
      },
      "enabled": true,
      "description": "PyEgeria MCP server for reports and Dr. Egeria commands"
    }
  },
  "settings": {
    "auto_discover_tools": true,
    "tool_timeout": 30,
    "max_concurrent_tools": 5,
    "enable_tool_caching": true,
    "cache_ttl": 300
  }
}
```

Don't put a user or password in this file. EA drops any `EGERIA_USER` / `EGERIA_PASSWORD` given here and starts the
`pyegeria` and `dr-egeria` children as EA's own service account (`EA_DAEMON_USER_ID` / `EA_DAEMON_USER_PASSWORD`,
TR-34). With no service account set, those two children are not started.

### 2. Environment Variables

For security, you can use environment variables instead of hardcoding credentials:

```bash
export EA_DAEMON_USER_ID="egeriaadvisornpa"
export EA_DAEMON_USER_PASSWORD="<from coco-user-directory.omsecrets>"
export EGERIA_VIEW_SERVER_URL="https://localhost:9443"
```

These are EA's own service-account credentials (TR-34), not a person's. The old `EGERIA_USER` / `EGERIA_PASSWORD` are still read, with a warning, until after 6.3.

Then in your config:

```json
{
  "mcpServers": {
    "pyegeria": {
      "command": "/path/to/python",
      "args": ["/path/to/pyegeria/core/mcp_server.py"],
      "env": {},
      "enabled": true
    }
  }
}
```

The MCP agent will automatically use environment variables as fallback.

## Usage

### Interactive CLI

The simplest way to use MCP tools is through the interactive CLI:

```bash
# With MCP tools enabled (default)
python examples/cli_with_mcp.py

# With custom config
python examples/cli_with_mcp.py --config /path/to/config.json

# Without MCP tools
python examples/cli_with_mcp.py --no-tools
```

**CLI Commands:**

```
>>> tools                    # List available tools
>>> execute <tool_name>      # Execute a tool interactively
>>> metrics                  # Show MCP metrics
>>> clear-cache              # Clear tool result cache
>>> query <text>             # Ask a question (Phase 10.2)
>>> help                     # Show help
>>> exit                     # Exit
```

### Python API

#### Basic Usage

```python
import asyncio
from advisor.mcp_agent import initialize_mcp_agent, shutdown_mcp_agent

async def main():
    # Initialize MCP agent
    agent = await initialize_mcp_agent()
    
    try:
        # List available tools
        tools = agent.get_available_tools()
        for tool in tools:
            print(f"{tool.name}: {tool.description}")
        
        # Execute a tool
        result = await agent.execute_tool(
            "generate_egeria_report",
            {
                "report_type": "asset",
                "guid": "xyz-123"
            }
        )
        print(f"Result: {result}")
        
    finally:
        # Shutdown
        await shutdown_mcp_agent()

asyncio.run(main())
```

#### Advanced Usage

```python
from advisor.mcp_agent import MCPAgent, MCPConfig, MCPServerConfig

# Create custom configuration
config = MCPConfig(
    servers={
        "pyegeria": MCPServerConfig(
            command="/path/to/python",
            args=["/path/to/mcp_server.py"],
            env={"EGERIA_USER": "user"},
            enabled=True
        )
    },
    tool_timeout=60,
    enable_tool_caching=True
)

# Create agent with custom config
agent = MCPAgent(config=config)
await agent.initialize()

# Execute multiple tools in parallel
results = await agent.execute_tools_parallel([
    {"name": "tool1", "arguments": {"arg": "value1"}},
    {"name": "tool2", "arguments": {"arg": "value2"}}
])

# Get metrics
metrics = agent.get_metrics()
print(f"Success rate: {metrics['success_rate']:.1%}")

await agent.shutdown()
```

## Tool Discovery

When the MCP agent initializes, it automatically discovers available tools from all enabled servers:

```python
agent = await initialize_mcp_agent()

# Get all tools
tools = agent.get_available_tools()

# Get specific tool
tool = agent.get_tool("generate_egeria_report")

# Get tool descriptions for LLM
tool_descriptions = agent.get_tool_descriptions()
```

## Tool Execution

### Direct Execution

```python
# Execute with default timeout (30s)
result = await agent.execute_tool(
    "generate_egeria_report",
    {"report_type": "asset", "guid": "xyz-123"}
)

# Execute with custom timeout
result = await agent.execute_tool(
    "long_running_tool",
    {"param": "value"},
    timeout=120
)

# Execute without cache
result = await agent.execute_tool(
    "tool_name",
    {"param": "value"},
    use_cache=False
)
```

### Parallel Execution

```python
# Execute multiple tools concurrently
results = await agent.execute_tools_parallel([
    {
        "name": "generate_report",
        "arguments": {"type": "asset", "guid": "123"}
    },
    {
        "name": "get_lineage",
        "arguments": {"guid": "456"}
    }
])

# Results are returned in the same order
# Exceptions are returned as exception objects
for i, result in enumerate(results):
    if isinstance(result, Exception):
        print(f"Tool {i} failed: {result}")
    else:
        print(f"Tool {i} result: {result}")
```

## Caching

Tool results are automatically cached to improve performance:

```python
# First call - executes tool
result1 = await agent.execute_tool("tool", {"arg": "value"})

# Second call - returns cached result (within TTL)
result2 = await agent.execute_tool("tool", {"arg": "value"})

# Clear cache
agent.clear_cache()

# Disable cache for specific call
result3 = await agent.execute_tool("tool", {"arg": "value"}, use_cache=False)
```

**Cache Configuration:**

```json
{
  "settings": {
    "enable_tool_caching": true,
    "cache_ttl": 300  // 5 minutes
  }
}
```

## Error Handling

```python
from advisor.mcp_client import (
    MCPError,
    MCPConnectionError,
    MCPToolNotFoundError,
    MCPToolExecutionError,
    MCPTimeoutError
)

try:
    result = await agent.execute_tool("tool_name", {"arg": "value"})
except MCPConnectionError as e:
    print(f"Connection failed: {e}")
except MCPToolNotFoundError as e:
    print(f"Tool not found: {e}")
except MCPToolExecutionError as e:
    print(f"Execution failed: {e}")
except MCPTimeoutError as e:
    print(f"Timeout: {e}")
except MCPError as e:
    print(f"MCP error: {e}")
```

## Metrics and Monitoring

Track MCP performance with built-in metrics:

```python
metrics = agent.get_metrics()

print(f"Tool invocations: {metrics['tool_invocations']}")
print(f"Successes: {metrics['tool_successes']}")
print(f"Failures: {metrics['tool_failures']}")
print(f"Success rate: {metrics['success_rate']:.1%}")
print(f"Avg execution time: {metrics['average_execution_time']:.2f}s")
print(f"Cache hits: {metrics['cache_hits']}")
print(f"Cache misses: {metrics['cache_misses']}")
```

## Configuration Reference

### Server Configuration

```json
{
  "command": "/path/to/executable",
  "args": ["arg1", "arg2"],
  "env": {
    "VAR1": "value1",
    "VAR2": "value2"
  },
  "enabled": true,
  "description": "Server description"
}
```

- **command**: Path to the MCP server executable
- **args**: Command-line arguments
- **env**: Environment variables (merged with system env)
- **enabled**: Whether to connect to this server
- **description**: Human-readable description

### Global Settings

```json
{
  "settings": {
    "auto_discover_tools": true,
    "tool_timeout": 30,
    "max_concurrent_tools": 5,
    "enable_tool_caching": true,
    "cache_ttl": 300
  }
}
```

- **auto_discover_tools**: Automatically discover tools on connect
- **tool_timeout**: Default timeout for tool execution (seconds)
- **max_concurrent_tools**: Maximum concurrent tool executions
- **enable_tool_caching**: Enable result caching
- **cache_ttl**: Cache time-to-live (seconds)

## Integration with RAG System

**Status:** ✅ Complete (Phase 10.2)

The RAG integration enables automatic tool invocation based on query analysis:

1. **Analyze Query**: Determine if tools are needed based on keywords
2. **RAG Search**: Get context from vector database
3. **Tool Selection**: LLM selects appropriate tools from available tools
4. **Tool Execution**: Execute selected tools via MCP agent
5. **Context Enhancement**: Add tool results to context
6. **Final Response**: Generate response with tool results

### Usage Example

```python
from advisor.tool_augmented_rag import get_tool_augmented_rag

# Initialize tool-augmented RAG
tool_rag = await get_tool_augmented_rag()

# Query with automatic tool invocation
result = await tool_rag.query_with_tools(
    "Generate a report for asset xyz-123",
    enable_tools=True
)

print(result['response'])
print(f"Tools used: {result['tools_used']}")
print(f"Tool results: {result['tool_results']}")
```

### Query Analysis

The system automatically detects when tools are needed based on:
- **Keywords**: "report", "generate", "create", "run", "execute", "invoke"
- **Query Type**: Imperative commands vs. informational questions
- **Context**: Previous conversation history

### Tool Invocation Flow

```
User Query → Query Analysis → RAG Search → LLM with Tools
                                              ↓
                                         Tool Calls?
                                              ↓
                                    Execute via MCP Agent
                                              ↓
                                    Add Results to Context
                                              ↓
                                    Generate Final Response
```


## MCP Server Development Guide

### Adding Enum Support to Tool Parameters

The Egeria Advisor CLI displays enum values to users when they're defined in the tool schema. This improves user experience by showing valid options.

#### What You Need to Do

**Simply add the `enum` field to your tool parameter schema.** No additional logic or validation code is needed!

#### Example: Before and After

**Before (without enum):**
```python
# In your MCP server tool definition
{
    "name": "describe_report",
    "description": "Describe a report specification",
    "inputSchema": {
        "type": "object",
        "properties": {
            "output_type": {
                "type": "string",
                "title": "Output Type",
                "description": "Format for the output",
                "default": "DICT"
            }
        },
        "required": ["output_type"]
    }
}
```

**After (with enum):**
```python
# In your MCP server tool definition
{
    "name": "describe_report",
    "description": "Describe a report specification",
    "inputSchema": {
        "type": "object",
        "properties": {
            "output_type": {
                "type": "string",
                "title": "Output Type",
                "description": "Format for the output",
                "default": "DICT",
                "enum": ["DICT", "JSON", "MARKDOWN"]  # ← Add this line
            }
        },
        "required": ["output_type"]
    }
}
```

#### What Happens in the CLI

**Without enum:**
```
* output_type (string)
  Format for the output
  Default: DICT
  Value: _
```

**With enum:**
```
* output_type (string)
  Format for the output
  Valid values: DICT, JSON, MARKDOWN
  Default: DICT
  Value: _
```

#### Why No Validation Code is Needed

The `enum` field is **pure metadata** for the JSON Schema:

1. **JSON Schema validators** (if you use them) automatically validate against enum
2. **The CLI** shows users the valid options, reducing invalid inputs
3. **Your existing code** that handles the parameter works exactly the same

#### Complete Example for PyEgeria MCP Server

```python
def get_tool_schema():
    """Return tool schema with enum support."""
    return {
        "name": "run_report",
        "description": "Run a report specification",
        "inputSchema": {
            "type": "object",
            "properties": {
                "report_spec": {
                    "type": "string",
                    "title": "Report Specification",
                    "description": "The name of the report specification to run"
                },
                "output_type": {
                    "type": "string",
                    "title": "Output Type",
                    "description": "Format for the output",
                    "default": "DICT",
                    "enum": ["DICT", "JSON", "MARKDOWN"]  # Add enum here
                },
                "max_results": {
                    "type": "integer",
                    "title": "Max Results",
                    "description": "Maximum number of results to return",
                    "default": 100,
                    "enum": [10, 50, 100, 500, 1000]  # Enum works for integers too
                }
            },
            "required": ["report_spec"]
        }
    }

# Your existing tool implementation doesn't change!
def run_report(report_spec: str, output_type: str = "DICT", max_results: int = 100):
    """Run the report - no changes needed here."""
    if output_type == "DICT":
        return generate_dict_output(report_spec, max_results)
    elif output_type == "JSON":
        return generate_json_output(report_spec, max_results)
    elif output_type == "MARKDOWN":
        return generate_markdown_output(report_spec, max_results)
```

#### Benefits

✅ **Better UX**: Users see valid options  
✅ **Self-documenting**: Schema shows what's allowed  
✅ **No code changes**: Your existing logic stays the same  
✅ **Standard JSON Schema**: Follows JSON Schema specification  
✅ **Reduces errors**: Users less likely to enter invalid values  

#### When to Use Enum

Use `enum` when a parameter has:
- A fixed set of valid values
- Predefined options (like output formats)
- Limited choices (like priority levels: "low", "medium", "high")
- Configuration options (like "enabled", "disabled")

**Don't use enum when:**
- Values are dynamic or user-generated (like report names)
- There are too many options (like all possible GUIDs)
- Values are free-form text

#### Testing Your Changes

After adding enum to your tool schema:

1. Restart your MCP server
2. Start the Egeria Advisor CLI: `egeria-advisor --agent --interactive`
3. Execute your tool: `/execute your_tool_name`
4. Verify that enum values are displayed in the parameter prompt

## PyEgeria MCP Server

The pyegeria MCP server provides tools for:

### Current Tools (Planned)

1. **generate_egeria_report**
   - Generate reports for assets, glossaries, lineage
   - Parameters: report_type, guid

2. **execute_dr_egeria_command**
   - Execute Dr. Egeria markdown commands
   - Parameters: command

3. **list_assets**
   - List assets with filters
   - Parameters: search_string, asset_type

4. **get_lineage**
   - Get lineage for an asset
   - Parameters: guid, depth

### Future Tools

- create_glossary_term
- update_asset_metadata
- run_governance_action
- get_classification_report

## Troubleshooting

### MCP Server Won't Start

```
✗ Failed to connect to MCP server pyegeria: Connection failed
```

**Solutions:**
1. Check that the command path is correct
2. Verify Python environment is activated
3. Check that mcp_server.py exists
4. Review server logs for errors

### Tool Not Found

```
✗ Tool not found: generate_report
```

**Solutions:**
1. Run `tools` command to see available tools
2. Check tool name spelling
3. Verify server is connected
4. Check server logs for tool registration

### Tool Execution Timeout

```
✗ Tool execution timed out after 30s
```

**Solutions:**
1. Increase timeout in config: `"tool_timeout": 60`
2. Check server performance
3. Verify network connectivity
4. Review tool implementation

### Connection Errors

```
✗ MCP server pyegeria failed to start: [Errno 2] No such file or directory
```

**Solutions:**
1. Verify command path is absolute
2. Check file permissions
3. Ensure Python environment is correct
4. Test command manually

## Best Practices

### 1. Use Environment Variables for Credentials

```bash
export EGERIA_PASSWORD="secret"
```

Never commit credentials to version control.

### 2. Enable Caching for Read-Only Operations

```json
{
  "settings": {
    "enable_tool_caching": true,
    "cache_ttl": 300
  }
}
```

### 3. Set Appropriate Timeouts

```json
{
  "settings": {
    "tool_timeout": 30  // Adjust based on tool complexity
  }
}
```

### 4. Monitor Metrics

```python
metrics = agent.get_metrics()
if metrics['success_rate'] < 0.9:
    logger.warning("Low tool success rate")
```

### 5. Handle Errors Gracefully

```python
try:
    result = await agent.execute_tool(name, args)
except MCPError as e:
    logger.error(f"Tool failed: {e}")
    # Fall back to alternative approach
```

### 6. Clean Up Resources

```python
try:
    agent = await initialize_mcp_agent()
    # Use agent
finally:
    await shutdown_mcp_agent()
```

## Security Considerations

### 1. Credential Management ⚠️

**Critical Security Practices:**

```bash
# ✅ GOOD: Use environment variables
export EGERIA_PASSWORD="secret"
export EGERIA_USER="admin"

# ❌ BAD: Never hardcode in config files
{
  "env": {
    "EGERIA_PASSWORD": "secret"  // DON'T DO THIS
  }
}
```

**Best Practices:**
- ✅ Use environment variables for ALL sensitive data
- ✅ Never commit credentials to version control
- ✅ Add `config/mcp_servers.json` to `.gitignore`
- ✅ Rotate credentials regularly (every 90 days)
- ✅ Use secure credential storage (e.g., system keyring, HashiCorp Vault)
- ✅ Use different credentials for dev/test/prod environments
- ✅ Implement credential expiration policies
- ✅ Audit credential access and usage

**Example Secure Configuration:**

```json
{
  "mcpServers": {
    "pyegeria": {
      "command": "/path/to/python",
      "args": ["/path/to/mcp_server.py"],
      "env": {
        "EGERIA_USER": "${EGERIA_USER}",
        "EGERIA_PASSWORD": "${EGERIA_PASSWORD}",
        "EGERIA_VIEW_SERVER_URL": "${EGERIA_VIEW_SERVER_URL}"
      },
      "enabled": true
    }
  }
}
```

### 2. Tool Execution Safety 🛡️

**Input Validation:**
```python
# Validate tool arguments before execution
def validate_tool_args(tool_name: str, arguments: dict) -> bool:
    """Validate tool arguments against schema."""
    tool = agent.get_tool(tool_name)
    if not tool:
        raise ValueError(f"Unknown tool: {tool_name}")
    
    # Validate required parameters
    required = tool.input_schema.get("required", [])
    for param in required:
        if param not in arguments:
            raise ValueError(f"Missing required parameter: {param}")
    
    return True
```

**Timeout Protection:**
```python
# Always use timeouts to prevent hanging
result = await agent.execute_tool(
    "long_running_tool",
    arguments,
    timeout=30  # Fail after 30 seconds
)
```

**Rate Limiting:**
```python
# Implement rate limiting for tool invocations
from datetime import datetime, timedelta

class RateLimiter:
    def __init__(self, max_calls: int, window: timedelta):
        self.max_calls = max_calls
        self.window = window
        self.calls = []
    
    def check_limit(self) -> bool:
        now = datetime.now()
        # Remove old calls outside window
        self.calls = [t for t in self.calls if now - t < self.window]
        
        if len(self.calls) >= self.max_calls:
            return False
        
        self.calls.append(now)
        return True

# Usage
limiter = RateLimiter(max_calls=10, window=timedelta(minutes=1))
if not limiter.check_limit():
    raise Exception("Rate limit exceeded")
```

**Monitoring:**
```python
# Monitor tool usage for anomalies
metrics = agent.get_metrics()
if metrics['failed_calls'] > 10:
    logger.warning("High tool failure rate detected")
    # Alert security team
```

### 3. Network Security 🔒

**HTTPS Configuration:**
```python
# Always use HTTPS for Egeria connections
EGERIA_VIEW_SERVER_URL = "https://egeria-server:9443"  # ✅ HTTPS

# Never use HTTP in production
EGERIA_VIEW_SERVER_URL = "http://egeria-server:9080"   # ❌ HTTP
```

**SSL Certificate Verification:**
```python
import ssl
import certifi

# Verify SSL certificates
ssl_context = ssl.create_default_context(cafile=certifi.where())
ssl_context.check_hostname = True
ssl_context.verify_mode = ssl.CERT_REQUIRED
```

**Network Segmentation:**
- Run MCP servers in isolated network segments
- Use firewalls to restrict access
- Implement VPN for remote connections
- Use private networks for internal communication

### 4. Process Isolation 🔐

**Subprocess Security:**
```python
# Run MCP servers with limited privileges
import subprocess
import os

# Drop privileges before starting server
def start_mcp_server():
    # Set resource limits
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))  # 60 sec CPU
    resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 512*1024*1024))  # 512MB RAM
    
    # Start with limited environment
    env = {
        'PATH': '/usr/bin:/bin',
        'EGERIA_USER': os.environ['EGERIA_USER'],
        'EGERIA_PASSWORD': os.environ['EGERIA_PASSWORD']
    }
    
    subprocess.Popen(
        ['/path/to/python', 'mcp_server.py'],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
```

### 5. Audit Logging 📝

**Log All Tool Invocations:**
```python
import logging
from datetime import datetime

logger = logging.getLogger('mcp_audit')

def audit_tool_call(user: str, tool: str, args: dict, result: str):
    """Audit log for tool invocations."""
    logger.info({
        'timestamp': datetime.now().isoformat(),
        'user': user,
        'tool': tool,
        'arguments': args,
        'result': result,
        'success': True
    })
```

### 6. Error Handling 🚨

**Never Expose Sensitive Information in Errors:**
```python
# ❌ BAD: Exposes credentials
try:
    result = await client.connect()
except Exception as e:
    logger.error(f"Connection failed: {e}")  # May contain password

# ✅ GOOD: Sanitize error messages
try:
    result = await client.connect()
except Exception as e:
    sanitized = str(e).replace(password, '***')
    logger.error(f"Connection failed: {sanitized}")
```

### Security Checklist

Before deploying MCP integration:

- [ ] All credentials stored in environment variables
- [ ] `config/mcp_servers.json` added to `.gitignore`
- [ ] HTTPS enabled for all Egeria connections
- [ ] SSL certificate verification enabled
- [ ] Tool execution timeouts configured
- [ ] Rate limiting implemented
- [ ] Audit logging enabled
- [ ] Error messages sanitized
- [ ] Resource limits set for MCP servers
- [ ] Network segmentation configured
- [ ] Security monitoring alerts configured
- [ ] Incident response plan documented

## Next Steps

### Phase 10.3: Agent CLI Integration (Planned)

**Goal:** Integrate MCP tools into main agent CLI

**Tasks:**
- [ ] Add MCP agent initialization to `AgentInteractiveSession`
- [ ] Implement `/tools` command to list available tools
- [ ] Implement `/execute <tool>` command for tool invocation
- [ ] Add `/metrics` command for MCP metrics
- [ ] Implement pretty printing for tool results
- [ ] Add tool execution to query flow
- [ ] Update agent CLI documentation

**Benefits:**
- Unified CLI experience with conversational agent + tools
- Tool execution within conversation context
- Automatic tool invocation based on conversation

### Phase 10.4: Testing & Documentation (Planned)

**Tasks:**
- [ ] Create mock MCP server for testing
- [ ] Write comprehensive unit tests
- [ ] Add integration tests
- [ ] Create more usage examples
- [ ] Add troubleshooting guide
- [ ] Create video tutorials
- [ ] Document security best practices

### Phase 10.5: Remote Server Support (Future)

**Goal:** Support remote MCP servers via SSE/HTTP

**Tasks:**
- [ ] Implement SSE transport for remote servers
- [ ] Add authentication for remote connections
- [ ] Implement connection pooling
- [ ] Add server health monitoring
- [ ] Document remote server setup

## References

- **MCP Protocol**: https://modelcontextprotocol.io/
- **Phase 10 Design**: `PHASE10_MCP_INTEGRATION.md` (archived out-of-repo 2026-09-03; `docs/history/` was gitignored and never in the repository). This guide supersedes it.
- **PyEgeria**: https://github.com/odpi/egeria-python
- **OpenAI Function Calling**: https://platform.openai.com/docs/guides/function-calling

---

**Status:** Phase 10.1 & 10.2 Complete
**Next Phase:** 10.3 - Agent CLI Integration
**Last Updated:** 2026-02-20

## Quick Reference

### Example CLIs Available Now

1. **Tool Execution CLI**: `examples/cli_with_mcp.py`
   ```bash
   cd /path/to/egeria-advisor
   python examples/cli_with_mcp.py
   ```

2. **Tool-Augmented RAG CLI**: `examples/cli_with_tools_and_rag.py`
   ```bash
   python examples/cli_with_tools_and_rag.py
   ```

### Configuration Files

- **Template**: `config/mcp_servers.json.example`
- **Your Config**: `config/mcp_servers.json` (create from template)
- **Add to .gitignore**: `config/mcp_servers.json`

### Security Reminders

⚠️ **NEVER commit credentials to git**
⚠️ **ALWAYS use environment variables**
⚠️ **ALWAYS use HTTPS in production**
⚠️ **ALWAYS implement timeouts**
⚠️ **ALWAYS validate tool inputs**