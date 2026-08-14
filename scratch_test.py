import asyncio
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def test_tool_call():
    server_url = "http://localhost:10015/mcp"
    
    print(f"Connecting to {server_url}...")
    try:
        async with streamablehttp_client(server_url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                print("Initializing session...")
                await session.initialize()
                
                print("Listing tools...")
                tools = await session.list_tools()
                print("Available tools:", [t.name for t in tools.tools])
                
                print("Calling query_postgres tool...")
                result = await session.call_tool(
                    "query_postgres", 
                    arguments={
                        "sql_query": "select * from averages limit 5;",
                        "question": "Test query"
                    }
                )
                print("Tool invocation result:")
                print(result)
    except Exception as e:
        print("Exception occurred:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_tool_call())
