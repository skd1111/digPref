/**
 * MCP server / tool metadata.
 */
export type McpServerStatus = 'stopped' | 'starting' | 'ready' | 'error';

export interface McpServerInfo {
  name: string;
  status: McpServerStatus;
  transport: 'stdio' | 'http';
  command?: string;
  args?: string[];
  error?: string;
}

export interface McpToolSpec {
  server: string;
  name: string;
  description?: string;
  inputSchema: Record<string, unknown>;
}