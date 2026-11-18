"""
MCP Agent for Egeria Advisor

High-level agent for managing multiple MCP servers and coordinating tool execution.
"""

import asyncio
import json
import shutil
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from advisor.mcp_client import (
    MCPClient,
    MCPSSEClient,
    MCPServerConfig,
    MCPTool,
    MCPResource,
    MCPError,
    MCPMetrics,
    ToolCache
)

logger = logging.getLogger(__name__)


def _resolve_dr_egeria_env(env: Dict[str, str]) -> Dict[str, str]:
    """Replace dr-egeria's placeholder EGERIA_ROOT_PATH with a real one.

    config/mcp_servers.json ships a placeholder rather than one developer's
    absolute path; unresolved, the subprocess starts against a directory that
    does not exist. Left alone when nothing resolves, so the caller's
    disable-on-unresolvable check still sees the placeholder.
    """
    from advisor.mcp_config import resolve_egeria_root_path
    root = resolve_egeria_root_path()
    if root:
        env = dict(env)
        env["EGERIA_ROOT_PATH"] = root
    return env


#: The MCP children that sign in to Egeria themselves, by pyegeria's own variable names (their code reads only
#: EGERIA_USER / EGERIA_USER_PASSWORD; neither takes a user's token per call). TR-34: they run as EA's own service
#: account, passed explicitly; never a person's persona from the JSON or the parent environment.
EGERIA_SIGN_IN_CHILDREN = ("pyegeria", "dr-egeria")
_CHILD_IDENTITY_VARS = ("EGERIA_USER", "EGERIA_PASSWORD", "EGERIA_USER_PASSWORD")


def _child_identity(name: str, env: Dict[str, str]) -> Tuple[Dict[str, str], bool]:
    """(env, ok) for an Egeria sign-in child: any identity in its JSON env dropped, and EA's service account set
    under the names the child reads. ok is False when EA has no service account: the child is then not started
    (one of them falls back to a built-in persona of its own otherwise)."""
    out = {k: v for k, v in env.items() if k not in _CHILD_IDENTITY_VARS}
    if name not in EGERIA_SIGN_IN_CHILDREN:
        return env, True
    from advisor.config import settings
    user, pwd = settings.daemon_user_id or "", settings.daemon_user_password or ""
    # Always set, so the child never inherits a shell's persona (MCPServerConfig merges os.environ under these).
    out["EGERIA_USER"], out["EGERIA_USER_PASSWORD"] = user, pwd
    return out, bool(user and pwd)


def _resolve_server_env(name: str, env: Dict[str, str]) -> Dict[str, str]:
    """
    Overlay resolved Egeria connection values onto a server's env dict before the
    MCP subprocess is spawned.

    config/mcp_servers.json's "pyegeria" env block ships with EGERIA_VIEW_SERVER_URL/
    EGERIA_VIEW_SERVER hardcoded to local Egeria defaults. advisor.mcp_config's
    get_pyegeria_platform_config() (env vars first, then this same JSON) is the
    single source of truth everywhere else in the app (auth.py, report_pipeline.py,
    egeria_context.py, dr_egeria_agent.py) -- but MCPServerConfig.get_env_with_defaults()
    (advisor/mcp_client.py) does `os.environ.copy(); env.update(self.env)`, so this
    JSON's literal values would otherwise always win over a .env override when the
    subprocess is actually launched, silently pointing report execution at the wrong
    Egeria instance even after .env is updated. Patching it in here, at config-load
    time, keeps get_env_with_defaults()'s general parent-env-as-fallback merge
    behavior correct for any other future server while ensuring "pyegeria" always
    gets the resolved connection.
    """
    if name == "dr-egeria":
        return _resolve_dr_egeria_env(env)
    if name != "pyegeria":
        return env
    from advisor.mcp_config import get_pyegeria_platform_config, resolve_report_specs_dir
    conn = get_pyegeria_platform_config()
    resolved = dict(env)
    if conn["platform_url"]:
        resolved["EGERIA_VIEW_SERVER_URL"] = conn["platform_url"]
    if conn["view_server"]:
        resolved["EGERIA_VIEW_SERVER"] = conn["view_server"]
    # config/mcp_servers.json's PYEGERIA_USER_REPORT_SPECS_DIR ships as a
    # relative "config/report_specs" so the JSON stays portable; resolve it
    # to an absolute path here for the same reason EGERIA_VIEW_SERVER_URL is
    # patched above — the subprocess doesn't share this process's cwd.
    report_specs_dir = resolve_report_specs_dir()
    if report_specs_dir:
        resolved["PYEGERIA_USER_REPORT_SPECS_DIR"] = report_specs_dir
    return resolved


def _resolve_server_command(name: str, command: Optional[str], args: List[str]) -> tuple:
    """
    Resolve the actual command/args to launch server `name`'s subprocess with.

    "pyegeria" and "dr-egeria" both get resolved here — see
    advisor.mcp_config.resolve_pyegeria_mcp_command() for the full
    ADVISOR_PYEGERIA_MCP_COMMAND / current-interpreter / config-file
    resolution order. Every other server (and pyegeria itself, when that
    resolver falls through to `None`) keeps using config/mcp_servers.json's
    command/args unchanged, exactly as before.
    """
    if name == "pyegeria":
        from advisor.mcp_config import resolve_pyegeria_mcp_command
        resolved = resolve_pyegeria_mcp_command()
        if resolved is None:
            return command, args
        return resolved[0], list(resolved[1])
    if name == "dr-egeria":
        from advisor.mcp_config import resolve_dr_egeria_mcp_command
        resolved = resolve_dr_egeria_mcp_command()
        if resolved is None:
            return command, args
        return resolved[0], list(resolved[1])
    return command, args


def _resolve_tool_timeout(default: int) -> int:
    """
    ADVISOR_MCP_TOOL_TIMEOUT env var overrides config/mcp_servers.json's
    settings.tool_timeout, same override-via-.env pattern as the Egeria
    connection settings. Local Egeria can tolerate a short timeout; a remote
    instance needs a more generous one for real network latency -- this was
    the actual cause of reports timing out against a remote Egeria instance
    even after the connection itself was pointed at the right host (30s,
    the previous hardcoded default, wasn't enough round-trip time).
    """
    raw = os.environ.get("ADVISOR_MCP_TOOL_TIMEOUT", "")
    if raw:
        try:
            return int(raw)
        except ValueError:
            logger.warning(f"Invalid ADVISOR_MCP_TOOL_TIMEOUT={raw!r}, using default {default}")
    return default


def _build_servers(raw: Dict[str, Any]) -> Dict[str, "MCPServerConfig"]:
    """Turn a raw ``mcpServers`` block into MCPServerConfigs.

    Shared by MCPConfig.from_file and .from_dict, which each used to build
    this inline. They had already drifted once: the "never spawn a command
    that is not there" guard was added to from_file only, so a dict-sourced
    config still launched an unresolved placeholder. One builder now, so a
    change to resolution or guarding cannot apply to only half the callers.
    """
    servers: Dict[str, MCPServerConfig] = {}
    for name, server_data in (raw or {}).items():
        resolved_command, resolved_args = _resolve_server_command(
            name, server_data.get("command"), server_data.get("args", [])
        )
        enabled = server_data.get("enabled", True)
        # config/mcp_servers.json carries placeholders for host-specific
        # paths, and a resolver that finds nothing leaves them in place.
        # Launching one produces `[Errno 2] No such file or directory:
        # '<path-to-...>'` every startup -- EA BACKLOG DP-1's symptom with a
        # different bogus path. Disable instead, and log why, so startup
        # degrades to "that server is off" rather than a connection error.
        if (
            enabled
            and server_data.get("transport", "stdio") == "stdio"
            and resolved_command
            and not shutil.which(resolved_command)
            and not Path(resolved_command).exists()
        ):
            logger.warning(
                f"MCP server {name!r} disabled: command {resolved_command!r} does not "
                "exist. If this is a placeholder, set the matching resolver's env var "
                "(ADVISOR_PYEGERIA_MCP_COMMAND / ADVISOR_DR_EGERIA_MCP_COMMAND / "
                "EGERIA_WORKSPACES) or edit configdata/mcp_servers.json."
            )
            enabled = False
        child_env, has_identity = _child_identity(name, _resolve_server_env(name, server_data.get("env", {})))
        if enabled and not has_identity:
            from advisor.config import NO_SERVICE_ACCOUNT
            logger.warning(f"MCP server {name!r} disabled: it signs in to Egeria itself, and {NO_SERVICE_ACCOUNT}")
            enabled = False
        servers[name] = MCPServerConfig(
            command=resolved_command,
            args=resolved_args,
            env=child_env,
            url=server_data.get("url"),
            transport=server_data.get("transport", "stdio"),
            headers=server_data.get("headers", {}),
            enabled=enabled,
            description=server_data.get("description", ""),
        )
    return servers


class MCPConfig:
    """MCP configuration manager."""

    def __init__(
        self,
        servers: Dict[str, MCPServerConfig],
        auto_discover_tools: bool = True,
        tool_timeout: int = 30,
        max_concurrent_tools: int = 5,
        enable_tool_caching: bool = True,
        cache_ttl: int = 300
    ):
        """
        Initialize MCP configuration.
        
        Args:
            servers: Dictionary of server name to server config
            auto_discover_tools: Automatically discover tools on connect
            tool_timeout: Default timeout for tool execution in seconds
            max_concurrent_tools: Maximum number of concurrent tool executions
            enable_tool_caching: Enable caching of tool results
            cache_ttl: Cache time-to-live in seconds
        """
        self.servers = servers
        self.auto_discover_tools = auto_discover_tools
        self.tool_timeout = tool_timeout
        self.max_concurrent_tools = max_concurrent_tools
        self.enable_tool_caching = enable_tool_caching
        self.cache_ttl = cache_ttl
    
    @classmethod
    def from_file(cls, path: str) -> 'MCPConfig':
        """
        Load configuration from JSON file.
        
        Args:
            path: Path to configuration file
            
        Returns:
            MCPConfig instance
        """
        with open(path, 'r') as f:
            data = json.load(f)
        
        servers = _build_servers(data.get("mcpServers", {}))

        settings = data.get("settings", {})

        return cls(
            servers=servers,
            auto_discover_tools=settings.get("auto_discover_tools", True),
            tool_timeout=_resolve_tool_timeout(settings.get("tool_timeout", 30)),
            max_concurrent_tools=settings.get("max_concurrent_tools", 5),
            enable_tool_caching=settings.get("enable_tool_caching", True),
            cache_ttl=settings.get("cache_ttl", 300)
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MCPConfig':
        """
        Create configuration from dictionary.

        Args:
            data: Configuration dictionary

        Returns:
            MCPConfig instance
        """
        servers = _build_servers(data.get("mcpServers", {}))

        
        settings = data.get("settings", {})
        
        return cls(
            servers=servers,
            auto_discover_tools=settings.get("auto_discover_tools", True),
            tool_timeout=_resolve_tool_timeout(settings.get("tool_timeout", 30)),
            max_concurrent_tools=settings.get("max_concurrent_tools", 5),
            enable_tool_caching=settings.get("enable_tool_caching", True),
            cache_ttl=settings.get("cache_ttl", 300)
        )
    
    def to_file(self, path: str) -> None:
        """
        Save configuration to JSON file.
        
        Args:
            path: Path to save configuration
        """
        data = {
            "mcpServers": {
                name: {
                    "command": config.command,
                    "args": config.args,
                    "env": config.env,
                    "enabled": config.enabled,
                    "description": config.description
                }
                for name, config in self.servers.items()
            },
            "settings": {
                "auto_discover_tools": self.auto_discover_tools,
                "tool_timeout": self.tool_timeout,
                "max_concurrent_tools": self.max_concurrent_tools,
                "enable_tool_caching": self.enable_tool_caching,
                "cache_ttl": self.cache_ttl
            }
        }
        
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
    
    def get_enabled_servers(self) -> List[str]:
        """
        Get list of enabled server names.
        
        Returns:
            List of enabled server names
        """
        return [
            name for name, config in self.servers.items()
            if config.enabled
        ]


class MCPAgent:
    """High-level agent for managing multiple MCP servers."""
    
    def __init__(self, config: Optional[MCPConfig] = None, config_path: Optional[str] = None):
        """
        Initialize MCP agent.
        
        Args:
            config: MCP configuration (if None, will try to load from config_path)
            config_path: Path to configuration file (default: config/mcp_servers.json)
        """
        if config is None:
            if config_path is None:
                config_path = str(Path(__file__).parent / "configdata" / "mcp_servers.json")
            
            if os.path.exists(config_path):
                self.config = MCPConfig.from_file(config_path)
            else:
                logger.warning(f"MCP config file not found: {config_path}")
                self.config = MCPConfig(servers={})
        else:
            self.config = config
        
        self.clients: Dict[str, MCPClient] = {}
        self.tools: Dict[str, MCPTool] = {}
        self.resources: Dict[str, MCPResource] = {}
        self.metrics = MCPMetrics()
        self.cache = ToolCache(ttl=self.config.cache_ttl) if self.config.enable_tool_caching else None
        self._initialized = False
    
    async def initialize(self) -> None:
        """Initialize all configured MCP servers."""
        if self._initialized:
            logger.warning("MCP agent already initialized")
            return
        
        logger.info("Initializing MCP agent...")
        
        enabled_servers = self.config.get_enabled_servers()
        if not enabled_servers:
            logger.warning("No enabled MCP servers configured")
            return
        
        # Connect to all enabled servers
        for server_name in enabled_servers:
            try:
                server_config = self.config.servers[server_name]
                # Choose transport: SSE for remote servers, stdio for local
                if server_config.is_remote():
                    client = MCPSSEClient(server_name, server_config)
                else:
                    client = MCPClient(server_name, server_config)

                await client.connect()
                self.clients[server_name] = client
                
                # Add tools from this server
                for tool in client.tools.values():
                    self.tools[tool.name] = tool
                
                # Add resources from this server
                for resource in client.resources.values():
                    self.resources[resource.uri] = resource
                
                logger.info(
                    f"Connected to {server_name}: "
                    f"{len(client.tools)} tools, {len(client.resources)} resources"
                )
                
            except Exception as e:
                logger.error(f"Failed to initialize server {server_name}: {e}")

        if not self.clients:
            # Every enabled server failed. Do NOT mark initialized: a caller
            # that sees _initialized=True with zero tools would report success
            # (the web pre-warm did exactly that) and never retry.
            raise ConnectionError(
                f"none of the enabled MCP servers connected: {', '.join(enabled_servers)}"
            )

        self._initialized = True
        logger.info(
            f"MCP agent initialized: {len(self.clients)} servers, "
            f"{len(self.tools)} tools, {len(self.resources)} resources"
        )
    
    async def shutdown(self) -> None:
        """Shutdown all MCP servers."""
        logger.info("Shutting down MCP agent...")
        
        for server_name, client in self.clients.items():
            try:
                await client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting from {server_name}: {e}")
        
        self.clients.clear()
        self.tools.clear()
        self.resources.clear()
        self._initialized = False
        
        logger.info("MCP agent shut down")
    
    def get_available_tools(self) -> List[MCPTool]:
        """
        Get all available tools from all servers.
        
        Returns:
            List of available tools
        """
        return list(self.tools.values())
    
    def get_tool(self, tool_name: str) -> Optional[MCPTool]:
        """
        Get a specific tool by name.
        
        Args:
            tool_name: Name of the tool
            
        Returns:
            MCPTool or None if not found
        """
        return self.tools.get(tool_name)
    
    async def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        use_cache: bool = True
    ) -> Any:
        """
        Execute a tool by name.
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            use_cache: Whether to use cached results
            
        Returns:
            Tool execution result
            
        Raises:
            MCPError: If tool execution fails
        """
        if not self._initialized:
            raise MCPError("MCP agent not initialized")
        
        tool = self.tools.get(tool_name)
        if not tool:
            raise MCPError(f"Tool not found: {tool_name}")
        
        # Check cache
        if use_cache and self.cache:
            cached_result = self.cache.get(tool_name, arguments)
            if cached_result is not None:
                self.metrics.cache_hits += 1
                logger.info(f"Using cached result for {tool_name}")
                return cached_result
            self.metrics.cache_misses += 1
        
        # Get client for this tool's server
        client = self.clients.get(tool.server)
        if not client:
            raise MCPError(f"Server not connected: {tool.server}")
        
        # Execute tool
        self.metrics.tool_invocations += 1
        start_time = asyncio.get_event_loop().time()
        
        try:
            result = await client.invoke_tool(
                tool_name,
                arguments,
                timeout=self.config.tool_timeout
            )
            
            execution_time = asyncio.get_event_loop().time() - start_time
            self.metrics.total_execution_time += execution_time
            self.metrics.tool_successes += 1
            
            # Cache result
            if use_cache and self.cache:
                self.cache.set(tool_name, arguments, result)
            
            return result
            
        except Exception as e:
            self.metrics.tool_failures += 1
            raise
    
    async def execute_tools_parallel(
        self,
        tool_calls: List[Dict[str, Any]]
    ) -> List[Any]:
        """
        Execute multiple tools in parallel.
        
        Args:
            tool_calls: List of tool calls with 'name' and 'arguments'
            
        Returns:
            List of results (or exceptions)
        """
        # Limit concurrent executions
        semaphore = asyncio.Semaphore(self.config.max_concurrent_tools)
        
        async def execute_with_semaphore(call: Dict[str, Any]) -> Any:
            async with semaphore:
                try:
                    return await self.execute_tool(
                        call["name"],
                        call["arguments"]
                    )
                except Exception as e:
                    return e
        
        tasks = [execute_with_semaphore(call) for call in tool_calls]
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    def get_tool_descriptions(self) -> List[Dict[str, Any]]:
        """
        Get tool descriptions for LLM function calling.
        
        Returns:
            List of tool descriptions in OpenAI format
        """
        return [tool.to_function_definition() for tool in self.tools.values()]
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Get MCP metrics.
        
        Returns:
            Dictionary of metrics
        """
        return self.metrics.to_dict()
    
    def clear_cache(self) -> None:
        """Clear the tool result cache."""
        if self.cache:
            self.cache.clear()
            logger.info("Tool cache cleared")


# Singleton instance
_mcp_agent: Optional[MCPAgent] = None


def get_mcp_agent(config: Optional[MCPConfig] = None, config_path: Optional[str] = None) -> MCPAgent:
    """
    Get or create the MCP agent singleton.
    
    Args:
        config: MCP configuration
        config_path: Path to configuration file
        
    Returns:
        MCPAgent instance
    """
    global _mcp_agent
    
    if _mcp_agent is None:
        _mcp_agent = MCPAgent(config=config, config_path=config_path)
    
    return _mcp_agent


async def initialize_mcp_agent(config: Optional[MCPConfig] = None, config_path: Optional[str] = None) -> MCPAgent:
    """
    Initialize and return the MCP agent.
    
    Args:
        config: MCP configuration
        config_path: Path to configuration file
        
    Returns:
        Initialized MCPAgent instance
    """
    agent = get_mcp_agent(config=config, config_path=config_path)
    if not agent._initialized:
        await agent.initialize()
    return agent


async def shutdown_mcp_agent() -> None:
    """Shutdown the MCP agent singleton."""
    global _mcp_agent
    
    if _mcp_agent:
        await _mcp_agent.shutdown()
        _mcp_agent = None