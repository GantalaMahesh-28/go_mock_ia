- mcp_app = mcp.http_app(path="/", transport="sse")
+ mcp_app = mcp.http_app(path="/")
- from mcp.client.sse import sse_client
+ from mcp.client.streamable_http import streamablehttp_client
  
- async with sse_client(server_sse_url, headers=headers) as (read_stream, write_stream):
+ async with streamablehttp_client(server_url) as (read_stream, write_stream, _):
