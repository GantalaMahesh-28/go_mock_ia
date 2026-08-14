import os
import asyncio
import logging
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ul_dashboard")

# Target MCP server configurations
MCP_SERVER_HOST = "localhost"
MCP_SERVER_PORT = os.getenv("MCP_SERVER_PORT", "10015")
MCP_SERVER_URL = f"http://{MCP_SERVER_HOST}:{MCP_SERVER_PORT}"

app = FastAPI(title="UL Insights Dashboard Client")

class QueryRequest(BaseModel):
    sql_query: str
    question: str

class TranslateRequest(BaseModel):
    question: str
    schema_info: str
    provider: str = "gemini"  # "gemini" or "azure_openai"
    api_key: str = ""
    # Azure OpenAI specific fields
    azure_endpoint: str = ""
    azure_deployment: str = ""
    azure_api_version: str = ""

@app.get("/api/schema")
async def get_schema():
    """Proxy schema request to the MCP Server."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{MCP_SERVER_URL}/schema", timeout=5.0)
            if response.status_code == 200:
                return response.json()
            else:
                raise HTTPException(status_code=response.status_code, detail="Failed to fetch schema from MCP Server.")
        except Exception as e:
            logger.error(f"Error fetching schema: {e}")
            raise HTTPException(status_code=503, detail=f"MCP Server is offline or unreachable on port {MCP_SERVER_PORT}")

@app.post("/api/query")
async def run_query(req: QueryRequest):
    """
    Connects to the MCP server over SSE using the mcp library client,
    calls the query_postgres tool, and returns the result.
    """
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    server_url = f"{MCP_SERVER_URL}/mcp"
    logger.info(f"Connecting to MCP Streamable HTTP endpoint: {server_url}")

    try:
        # Establish client connection to MCP Server using Streamable HTTP
        async with streamablehttp_client(server_url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                # Handshake
                await session.initialize()
                
                # Execute tool
                tool_name = "query_postgres"
                tool_args = {
                    "sql_query": req.sql_query,
                    "question": req.question
                }
                
                logger.info(f"Calling tool '{tool_name}' on MCP server...")
                result = await session.call_tool(tool_name, arguments=tool_args)
                
                # Extract text content
                content_text = ""
                for content in result.content:
                    if hasattr(content, 'text'):
                        content_text += content.text
                    elif isinstance(content, dict) and 'text' in content:
                        content_text += content['text']
                
                return {"result": content_text}
                
    except Exception as e:
        logger.error(f"Error executing MCP tool call: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"MCP invocation failed: {str(e)}. Make sure server.py is running on port {MCP_SERVER_PORT}."
        )

@app.post("/api/translate")
async def translate_question(req: TranslateRequest):
    """
    Translates a natural language question into a SQL query using Gemini API or Azure OpenAI API.
    If no API key / configurations are provided, falls back to a template matching mock SQL logic.
    """
    question = req.question.strip().lower()
    
    prompt = f"""You are a PostgreSQL Text-to-SQL converter for a Unilever insights tool.
Convert the user's question into a clean PostgreSQL query.

Database Schema Context:
{req.schema_info}

Rules:
1. ONLY return the raw SQL query. Do not wrap it in markdown code blocks like ```sql ... ```.
2. Keep the query read-only (SELECT statements only).
3. Do not add explanations.

User Question: {req.question}"""

    # Helper function to clean text-to-sql output
    def clean_sql(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) > 2:
                # Remove starting markdown fence (e.g. ```sql or ```) and closing fence
                start_idx = 1
                if lines[0].strip().replace("```", "").lower() == "sql":
                    start_idx = 1
                content_lines = lines[start_idx:-1]
                text = "\n".join(content_lines).strip()
            else:
                text = text.replace("```", "").strip()
        return text

    # Azure OpenAI flow
    if req.provider == "azure_openai":
        endpoint = req.azure_endpoint.strip() or os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
        api_key = req.api_key.strip() or os.getenv("AZURE_OPENAI_API_KEY", "").strip()
        deployment = req.azure_deployment.strip() or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "").strip()
        api_version = req.azure_api_version.strip() or os.getenv("AZURE_OPENAI_API_VERSION", "2024-05-01-preview").strip()

        if not endpoint or not api_key or not deployment:
            # Check if we can fall back to local rule-based mock
            if not api_key:
                return run_mock_translation(question)
            raise HTTPException(
                status_code=400, 
                detail="Azure OpenAI endpoint, key, or deployment name is missing. Please configure them in Settings or the .env file."
            )

        url = f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
        headers = {
            "api-key": api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "messages": [
                {"role": "system", "content": "You are a database Text-to-SQL translator. Return ONLY the SQL query without any explanation or markdown formatting."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=payload, timeout=12.0)
                if response.status_code == 200:
                    data = response.json()
                    raw_text = data["choices"][0]["message"]["content"]
                    sql_query = clean_sql(raw_text)
                    return {"sql": sql_query, "info": f"Generated SQL via Azure OpenAI (deployment: {deployment})."}
                else:
                    raise HTTPException(
                        status_code=response.status_code, 
                        detail=f"Azure OpenAI service error: {response.text}"
                    )
        except Exception as e:
            logger.error(f"Error calling Azure OpenAI: {e}")
            raise HTTPException(status_code=500, detail=f"Azure OpenAI translation failed: {str(e)}")

    # Gemini flow
    else:
        api_key = req.api_key.strip() or os.getenv("GEMINI_API_KEY", "").strip()
        
        if not api_key:
            return run_mock_translation(question)
            
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
        headers = {"Content-Type": "application/json"}
        params = {"key": api_key}
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, params=params, json=payload, timeout=10.0)
                if response.status_code == 200:
                    data = response.json()
                    raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                    sql_query = clean_sql(raw_text)
                    return {"sql": sql_query, "info": "Generated SQL via Gemini 1.5 Flash."}
                else:
                    raise HTTPException(status_code=response.status_code, detail=f"Gemini API returned error: {response.text}")
        except Exception as e:
            logger.error(f"Error calling Gemini: {e}")
            raise HTTPException(status_code=500, detail=f"Gemini translation failed: {str(e)}")

def run_mock_translation(question: str):
    """Fallback logic when no API credentials are provided."""
    if "product" in question or "sales" in question:
        mock_sql = "SELECT name, category, price, stock FROM products LIMIT 10;"
        return {"sql": mock_sql, "info": "Generated SQL using local keyword parser (No LLM API Key)."}
    elif "customer" in question or "user" in question:
        mock_sql = "SELECT id, name, email, created_at FROM users LIMIT 10;"
        return {"sql": mock_sql, "info": "Generated SQL using local keyword parser (No LLM API Key)."}
    else:
        mock_sql = "SELECT * FROM information_schema.tables WHERE table_schema = 'public';"
        return {"sql": mock_sql, "info": "Could not identify keywords. Listing tables instead."}

# HTML Single Page Dashboard
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>UL Insights Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-dark: #0f172a;
            --bg-card: #1e293b;
            --border-color: #334155;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --primary-green: #00a651;
            --primary-green-glow: rgba(0, 166, 81, 0.4);
            --primary-green-dark: #008740;
            --accent-glow: rgba(0, 166, 81, 0.15);
            --danger-red: #ef4444;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Inter', sans-serif;
        }

        body {
            background-color: var(--bg-dark);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            overflow-x: hidden;
        }

        h1, h2, h3, h4 {
            font-family: 'Outfit', sans-serif;
        }

        /* Top Bar Styling */
        header {
            background: rgba(30, 41, 59, 0.7);
            backdrop-filter: blur(10px);
            border-bottom: 1px solid var(--border-color);
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            z-index: 100;
        }

        .logo-section {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .logo-section h1 {
            font-size: 1.5rem;
            font-weight: 700;
            background: linear-gradient(135deg, #ffffff 50%, var(--primary-green) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .status-dot {
            width: 10px;
            height: 10px;
            background-color: var(--primary-green);
            border-radius: 50%;
            box-shadow: 0 0 10px var(--primary-green);
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0% { transform: scale(0.9); box-shadow: 0 0 0 0 rgba(0, 166, 81, 0.7); }
            70% { transform: scale(1); box-shadow: 0 0 0 8px rgba(0, 166, 81, 0); }
            100% { transform: scale(0.9); box-shadow: 0 0 0 0 rgba(0, 166, 81, 0); }
        }

        .nav-actions {
            display: flex;
            gap: 1rem;
            align-items: center;
        }

        button.btn-icon {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            padding: 0.5rem;
            border-radius: 0.5rem;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s ease;
        }

        button.btn-icon:hover {
            border-color: var(--primary-green);
            background: rgba(0, 166, 81, 0.05);
        }

        /* Container Layout */
        .app-container {
            display: flex;
            flex: 1;
            padding: 2rem;
            gap: 2rem;
            max-width: 1600px;
            margin: 0 auto;
            width: 100%;
        }

        /* Sidebar Schema Viewer */
        .sidebar {
            flex: 0 0 300px;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 1rem;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            gap: 1rem;
            max-height: calc(100vh - 120px);
            overflow-y: auto;
        }

        .sidebar-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 0.75rem;
        }

        .sidebar-header h2 {
            font-size: 1.1rem;
            font-weight: 600;
        }

        .schema-list {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
        }

        .schema-table-item {
            border: 1px solid var(--border-color);
            border-radius: 0.5rem;
            overflow: hidden;
        }

        .schema-table-header {
            background: rgba(51, 65, 85, 0.3);
            padding: 0.5rem 0.75rem;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            font-weight: 600;
            font-size: 0.9rem;
            transition: background 0.2s;
        }

        .schema-table-header:hover {
            background: rgba(51, 65, 85, 0.5);
        }

        .schema-columns {
            padding: 0.5rem 0.75rem;
            background: rgba(15, 23, 42, 0.4);
            display: none;
            flex-direction: column;
            gap: 0.25rem;
            font-size: 0.8rem;
            color: var(--text-secondary);
        }

        /* Main Workbench */
        .workbench {
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 2rem;
        }

        .card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 1rem;
            padding: 2rem;
            box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
        }

        /* Tab Layout */
        .tabs {
            display: flex;
            gap: 0.5rem;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 1.5rem;
            padding-bottom: 1px;
        }

        .tab-btn {
            background: none;
            border: none;
            color: var(--text-secondary);
            padding: 0.75rem 1.25rem;
            cursor: pointer;
            font-weight: 500;
            font-size: 0.95rem;
            border-bottom: 2px solid transparent;
            transition: all 0.2s ease;
        }

        .tab-btn:hover {
            color: var(--text-primary);
        }

        .tab-btn.active {
            color: var(--primary-green);
            border-bottom-color: var(--primary-green);
        }

        .tab-content {
            display: none;
            flex-direction: column;
            gap: 1.25rem;
        }

        .tab-content.active {
            display: flex;
        }

        .input-group {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        .input-group label {
            font-size: 0.85rem;
            font-weight: 500;
            color: var(--text-secondary);
        }

        input[type="text"], textarea {
            background-color: var(--bg-dark);
            border: 1px solid var(--border-color);
            border-radius: 0.5rem;
            color: var(--text-primary);
            padding: 0.75rem 1rem;
            font-size: 0.95rem;
            outline: none;
            transition: border-color 0.2s, box-shadow 0.2s;
        }

        input[type="text"]:focus, textarea:focus {
            border-color: var(--primary-green);
            box-shadow: 0 0 0 2px var(--primary-green-glow);
        }

        textarea {
            resize: vertical;
            min-height: 120px;
            font-family: 'Courier New', Courier, monospace;
        }

        .action-bar {
            display: flex;
            justify-content: flex-end;
            gap: 1rem;
            margin-top: 1rem;
        }

        /* Buttons styling */
        .btn {
            padding: 0.75rem 1.5rem;
            border-radius: 0.5rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            outline: none;
            font-size: 0.95rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .btn-primary {
            background-color: var(--primary-green);
            border: 1px solid var(--primary-green);
            color: #ffffff;
        }

        .btn-primary:hover {
            background-color: var(--primary-green-dark);
            border-color: var(--primary-green-dark);
            box-shadow: 0 0 15px var(--primary-green-glow);
        }

        .btn-secondary {
            background-color: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-primary);
        }

        .btn-secondary:hover {
            background: rgba(255, 255, 255, 0.05);
            border-color: var(--text-secondary);
        }

        /* Response Panel */
        .response-panel {
            min-height: 200px;
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }

        .response-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 1rem;
        }

        .response-header h3 {
            font-size: 1.25rem;
            font-weight: 600;
        }

        /* Markdown Table Styling */
        .md-table-container {
            width: 100%;
            overflow-x: auto;
            border-radius: 0.5rem;
            border: 1px solid var(--border-color);
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 0.9rem;
        }

        th {
            background-color: rgba(51, 65, 85, 0.5);
            padding: 0.75rem 1rem;
            font-weight: 600;
            border-bottom: 2px solid var(--border-color);
            color: var(--text-primary);
        }

        td {
            padding: 0.75rem 1rem;
            border-bottom: 1px solid var(--border-color);
            color: var(--text-secondary);
        }

        tr:nth-child(even) td {
            background-color: rgba(30, 41, 59, 0.3);
        }

        /* Download Card Banner */
        .download-banner {
            background: linear-gradient(135deg, rgba(0, 166, 81, 0.15) 0%, rgba(0, 0, 0, 0) 100%);
            border: 1px solid var(--primary-green);
            padding: 1.25rem 1.5rem;
            border-radius: 0.75rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
            animation: fadeIn 0.4s ease;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .download-info h4 {
            color: var(--primary-green);
            font-size: 1.1rem;
            margin-bottom: 0.25rem;
        }

        .download-info p {
            color: var(--text-secondary);
            font-size: 0.85rem;
        }

        /* Settings Modal */
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(15, 23, 42, 0.8);
            backdrop-filter: blur(5px);
            z-index: 1000;
            display: none;
            justify-content: center;
            align-items: center;
        }

        .modal {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 1rem;
            width: 100%;
            max-width: 500px;
            padding: 2rem;
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }

        .modal-title {
            font-size: 1.25rem;
            font-weight: 700;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        /* Loading Spinner */
        .spinner {
            border: 3px solid rgba(255, 255, 255, 0.1);
            border-top: 3px solid var(--primary-green);
            border-radius: 50%;
            width: 20px;
            height: 20px;
            animation: spin 1s linear infinite;
            display: none;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        .empty-state {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            color: var(--text-secondary);
            text-align: center;
            gap: 1rem;
            padding: 4rem 2rem;
        }

        .empty-state svg {
            color: var(--border-color);
            width: 48px;
            height: 48px;
        }

        .error-box {
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid var(--danger-red);
            color: #fca5a5;
            padding: 1rem 1.25rem;
            border-radius: 0.5rem;
            font-size: 0.9rem;
            display: none;
            word-break: break-all;
        }
    </style>
</head>
<body>

    <!-- Header Section -->
    <header>
        <div class="logo-section">
            <div class="status-dot"></div>
            <h1>Unilever Insights Engine</h1>
        </div>
        <div class="nav-actions">
            <button class="btn-icon" id="openSettingsBtn" title="Configurations">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.1a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"></path><circle cx="12" cy="12" r="3"></circle></svg>
            </button>
        </div>
    </header>

    <div class="app-container">
        <!-- Schema Sidebar -->
        <aside class="sidebar">
            <div class="sidebar-header">
                <h2>DB Schema Explorer</h2>
                <button class="btn-icon" id="refreshSchemaBtn" title="Refresh Schema">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
                </button>
            </div>
            <div class="schema-list" id="schemaList">
                <div style="text-align: center; color: var(--text-secondary); font-size: 0.85rem; padding: 2rem 0;">
                    Loading database schema...
                </div>
            </div>
        </aside>

        <!-- Main workbench area -->
        <main class="workbench">
            <div class="card">
                <!-- Navigation Tabs -->
                <div class="tabs">
                    <button class="tab-btn active" onclick="switchTab('direct-sql')">Direct SQL Query</button>
                    <button class="tab-btn" onclick="switchTab('ai-agent')">NL Agent (Gemini AI)</button>
                </div>

                <!-- Error Box -->
                <div class="error-box" id="errorBox"></div>

                <!-- Direct SQL Tab -->
                <div class="tab-content active" id="direct-sql-tab">
                    <div class="input-group">
                        <label for="sqlQueryInput">PostgreSQL Query</label>
                        <textarea id="sqlQueryInput" placeholder="SELECT * FROM products LIMIT 10;"></textarea>
                    </div>
                    <div class="input-group">
                        <label for="sqlQuestionInput">Report Summary Title / User Context</label>
                        <input type="text" id="sqlQuestionInput" placeholder="e.g. Sales summary report for Dove product category.">
                    </div>
                    <div class="action-bar">
                        <div class="spinner" id="sqlSpinner"></div>
                        <button class="btn btn-primary" id="runSqlQueryBtn">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                            Run Query & Export
                        </button>
                    </div>
                </div>

                <!-- AI Agent Tab -->
                <div class="tab-content" id="ai-agent-tab">
                    <div class="input-group">
                        <label for="aiQuestionInput">Ask in Natural Language</label>
                        <textarea id="aiQuestionInput" style="min-height: 80px;" placeholder="Show me total revenue and units sold grouped by product category."></textarea>
                    </div>
                    <div class="action-bar" style="margin-bottom: 1rem;">
                        <div class="spinner" id="translateSpinner"></div>
                        <button class="btn btn-secondary" id="translateBtn">
                            Translate to SQL
                        </button>
                    </div>
                    <div class="input-group" id="translatedSqlGroup" style="display: none;">
                        <label for="translatedSqlQuery">Generated PostgreSQL Query</label>
                        <textarea id="translatedSqlQuery"></textarea>
                        <div style="font-size: 0.8rem; color: var(--text-secondary); display: flex; align-items: center; gap: 0.25rem; margin-top: 0.25rem;">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
                            <span id="translationInfo"></span>
                        </div>
                    </div>
                    <div class="action-bar" id="aiExecuteBar" style="display: none;">
                        <div class="spinner" id="aiSpinner"></div>
                        <button class="btn btn-primary" id="runAiQueryBtn">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                            Execute & Export
                        </button>
                    </div>
                </div>
            </div>

            <!-- Query results section -->
            <div class="card response-panel">
                <div class="response-header">
                    <h3>Execution Output</h3>
                    <div id="resultCount" style="font-size: 0.9rem; color: var(--text-secondary);"></div>
                </div>

                <div id="resultsContainer" class="empty-state">
                    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
                    <div>
                        <p style="font-weight: 500; font-size: 1.05rem; margin-bottom: 0.25rem;">No Execution Context yet</p>
                        <p style="font-size: 0.85rem;">Input a SQL query or write a question above to execute.</p>
                    </div>
                </div>
            </div>
        </main>
    </div>

    <!-- Configuration Modal -->
    <div class="modal-overlay" id="settingsModal">
        <div class="modal" style="max-width: 550px;">
            <div class="modal-title">
                <span>Credentials & Configuration</span>
                <button class="btn-icon" onclick="closeSettings()" style="border: none; background: transparent;">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
            </div>
            
            <p style="font-size: 0.85rem; color: var(--text-secondary); line-height: 1.4;">
                Select your preferred AI provider to automatically translate natural language statements into SQL. You can also specify configurations here to override default environment variables.
            </p>

            <div class="input-group">
                <label for="aiProviderSelect">AI Provider</label>
                <select id="aiProviderSelect" onchange="toggleProviderSettings()" style="background-color: var(--bg-dark); border: 1px solid var(--border-color); border-radius: 0.5rem; color: var(--text-primary); padding: 0.75rem; font-size: 0.95rem; outline: none; cursor: pointer;">
                    <option value="gemini">Gemini API</option>
                    <option value="azure_openai">Azure OpenAI Service</option>
                </select>
            </div>

            <!-- Gemini Settings Panel -->
            <div id="geminiSettingsPanel" style="display: flex; flex-direction: column; gap: 1rem;">
                <div class="input-group">
                    <label for="geminiApiKeyInput">Gemini API Key</label>
                    <input type="password" id="geminiApiKeyInput" placeholder="AIzaSy...">
                </div>
            </div>

            <!-- Azure OpenAI Settings Panel -->
            <div id="azureSettingsPanel" style="display: none; flex-direction: column; gap: 1rem;">
                <div class="input-group">
                    <label for="azureEndpointInput">Azure Endpoint URL</label>
                    <input type="text" id="azureEndpointInput" placeholder="https://your-resource.openai.azure.com/">
                </div>
                <div class="input-group">
                    <label for="azureApiKeyInput">Azure API Key</label>
                    <input type="password" id="azureApiKeyInput" placeholder="Enter key...">
                </div>
                <div class="input-group">
                    <label for="azureDeploymentInput">Deployment Name</label>
                    <input type="text" id="azureDeploymentInput" placeholder="e.g. gpt-4o, gpt-35-turbo">
                </div>
                <div class="input-group">
                    <label for="azureApiVersionInput">API Version</label>
                    <input type="text" id="azureApiVersionInput" placeholder="e.g. 2024-05-01-preview" value="2024-05-01-preview">
                </div>
            </div>

            <div class="action-bar">
                <button class="btn btn-secondary" onclick="closeSettings()">Cancel</button>
                <button class="btn btn-primary" onclick="saveSettings()">Save Settings</button>
            </div>
        </div>
    </div>

    <script>
        let currentTab = 'direct-sql';
        let dbSchemaString = '';

        // Tab Switching
        function switchTab(tabId) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            
            if (tabId === 'direct-sql') {
                document.querySelector('.tab-btn:nth-child(1)').classList.add('active');
                document.getElementById('direct-sql-tab').classList.add('active');
            } else {
                document.querySelector('.tab-btn:nth-child(2)').classList.add('active');
                document.getElementById('ai-agent-tab').classList.add('active');
            }
            currentTab = tabId;
            hideError();
        }

        // Error Handling
        function showError(message) {
            const errorBox = document.getElementById('errorBox');
            errorBox.textContent = message;
            errorBox.style.display = 'block';
            errorBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        // Settings Handling
        const openSettingsBtn = document.getElementById('openSettingsBtn');
        const settingsModal = document.getElementById('settingsModal');
        
        function toggleProviderSettings() {
            const provider = document.getElementById('aiProviderSelect').value;
            if (provider === 'gemini') {
                document.getElementById('geminiSettingsPanel').style.display = 'flex';
                document.getElementById('azureSettingsPanel').style.display = 'none';
            } else {
                document.getElementById('geminiSettingsPanel').style.display = 'none';
                document.getElementById('azureSettingsPanel').style.display = 'flex';
            }
        }
        
        openSettingsBtn.addEventListener('click', () => {
            const provider = localStorage.getItem('ai_provider') || 'gemini';
            document.getElementById('aiProviderSelect').value = provider;
            
            document.getElementById('geminiApiKeyInput').value = localStorage.getItem('gemini_api_key') || '';
            document.getElementById('azureEndpointInput').value = localStorage.getItem('azure_endpoint') || '';
            document.getElementById('azureApiKeyInput').value = localStorage.getItem('azure_api_key') || '';
            document.getElementById('azureDeploymentInput').value = localStorage.getItem('azure_deployment') || '';
            document.getElementById('azureApiVersionInput').value = localStorage.getItem('azure_api_version') || '2024-05-01-preview';
            
            toggleProviderSettings();
            settingsModal.style.display = 'flex';
        });

        function closeSettings() {
            settingsModal.style.display = 'none';
        }

        function saveSettings() {
            const provider = document.getElementById('aiProviderSelect').value;
            localStorage.setItem('ai_provider', provider);
            
            localStorage.setItem('gemini_api_key', document.getElementById('geminiApiKeyInput').value.trim());
            localStorage.setItem('azure_endpoint', document.getElementById('azureEndpointInput').value.trim());
            localStorage.setItem('azure_api_key', document.getElementById('azureApiKeyInput').value.trim());
            localStorage.setItem('azure_deployment', document.getElementById('azureDeploymentInput').value.trim());
            localStorage.setItem('azure_api_version', document.getElementById('azureApiVersionInput').value.trim());
            
            // Also update tab label in the UI
            updateUiTabLabels();
            
            closeSettings();
        }
        
        function updateUiTabLabels() {
            const provider = localStorage.getItem('ai_provider') || 'gemini';
            const agentTabBtn = document.querySelector('.tabs button:nth-child(2)');
            if (agentTabBtn) {
                if (provider === 'azure_openai') {
                    agentTabBtn.textContent = 'NL Agent (Azure OpenAI)';
                } else {
                    agentTabBtn.textContent = 'NL Agent (Gemini AI)';
                }
            }
        }

        function hideError() {
            document.getElementById('errorBox').style.display = 'none';
        }

        // Fetch DB Schema Info
        async function fetchDbSchema() {
            const schemaList = document.getElementById('schemaList');
            try {
                const response = await fetch('/api/schema');
                const data = await response.json();
                
                if (data.schema) {
                    dbSchemaString = data.schema;
                    renderSchemaList(data.schema);
                } else {
                    schemaList.innerHTML = `<div style="text-align: center; color: var(--danger-red); font-size: 0.85rem; padding: 2rem 0;">Schema payload empty</div>`;
                }
            } catch (err) {
                schemaList.innerHTML = `
                    <div style="text-align: center; color: var(--danger-red); font-size: 0.85rem; padding: 1rem 0;">
                        Failed to load schema.<br>Is MCP server online?
                    </div>
                `;
            }
        }

        function renderSchemaList(schemaText) {
            const schemaList = document.getElementById('schemaList');
            schemaList.innerHTML = '';
            
            // Parse custom text schema
            const lines = schemaText.split('\\n');
            let currentTable = null;
            let currentColumns = [];
            
            const tables = [];
            
            for (let line of lines) {
                if (line.startsWith('Table: ')) {
                    if (currentTable) {
                        tables.push({ name: currentTable, columns: [...currentColumns] });
                    }
                    currentTable = line.replace('Table: ', '').trim();
                    currentColumns = [];
                } else if (line.startsWith('  - ')) {
                    currentColumns.push(line.replace('  - ', '').trim());
                }
            }
            if (currentTable) {
                tables.push({ name: currentTable, columns: currentColumns });
            }
            
            if (tables.length === 0) {
                schemaList.innerHTML = `<div style="text-align: center; color: var(--text-secondary); font-size: 0.85rem; padding: 2rem 0;">No active public tables.</div>`;
                return;
            }

            tables.forEach(table => {
                const item = document.createElement('div');
                item.className = 'schema-table-item';
                
                const header = document.createElement('div');
                header.className = 'schema-table-header';
                header.innerHTML = `
                    <span>${table.name}</span>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="chevron"><polyline points="6 9 12 15 18 9"></polyline></svg>
                `;
                
                const colsDiv = document.createElement('div');
                colsDiv.className = 'schema-columns';
                table.columns.forEach(col => {
                    const colSpan = document.createElement('span');
                    colSpan.textContent = col;
                    colsDiv.appendChild(colSpan);
                });
                
                header.addEventListener('click', () => {
                    const isOpen = colsDiv.style.display === 'flex';
                    colsDiv.style.display = isOpen ? 'none' : 'flex';
                    header.querySelector('.chevron').style.transform = isOpen ? 'rotate(0deg)' : 'rotate(180deg)';
                });
                
                item.appendChild(header);
                item.appendChild(colsDiv);
                schemaList.appendChild(item);
            });
        }

        document.getElementById('refreshSchemaBtn').addEventListener('click', fetchDbSchema);

        // Run SQL query directly
        document.getElementById('runSqlQueryBtn').addEventListener('click', async () => {
            const query = document.getElementById('sqlQueryInput').value.trim();
            const question = document.getElementById('sqlQuestionInput').value.trim() || 'Custom SQL Report';
            
            if (!query) {
                showError("Please enter a SQL query first.");
                return;
            }
            
            hideError();
            setLoading('sql', true);
            
            try {
                const response = await fetch('/api/query', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ sql_query: query, question: question })
                });
                const data = await response.json();
                
                if (response.ok) {
                    renderOutput(data.result);
                } else {
                    showError(data.detail || "Query failed to execute.");
                }
            } catch (err) {
                showError("Network error calling API backend.");
            } finally {
                setLoading('sql', false);
            }
        });

        // Translate NL question to SQL
        document.getElementById('translateBtn').addEventListener('click', async () => {
            const question = document.getElementById('aiQuestionInput').value.trim();
            if (!question) {
                showError("Please write a question for the AI model.");
                return;
            }
            
            hideError();
            setLoading('translate', true);
            
            const provider = localStorage.getItem('ai_provider') || 'gemini';
            let payload = {
                question: question,
                schema_info: dbSchemaString,
                provider: provider
            };
            
            if (provider === 'azure_openai') {
                payload.api_key = localStorage.getItem('azure_api_key') || '';
                payload.azure_endpoint = localStorage.getItem('azure_endpoint') || '';
                payload.azure_deployment = localStorage.getItem('azure_deployment') || '';
                payload.azure_api_version = localStorage.getItem('azure_api_version') || '';
            } else {
                payload.api_key = localStorage.getItem('gemini_api_key') || '';
            }
            
            try {
                const response = await fetch('/api/translate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                
                if (response.ok) {
                    document.getElementById('translatedSqlQuery').value = data.sql;
                    document.getElementById('translationInfo').textContent = data.info;
                    
                    document.getElementById('translatedSqlGroup').style.display = 'flex';
                    document.getElementById('aiExecuteBar').style.display = 'flex';
                } else {
                    showError(data.detail || "Translation failed.");
                }
            } catch (err) {
                showError("Error connecting to dashboard backend translator.");
            } finally {
                setLoading('translate', false);
            }
        });

        // Run SQL generated from AI Agent
        document.getElementById('runAiQueryBtn').addEventListener('click', async () => {
            const query = document.getElementById('translatedSqlQuery').value.trim();
            const question = document.getElementById('aiQuestionInput').value.trim();
            
            if (!query) {
                showError("No query to run. Please translate first.");
                return;
            }
            
            hideError();
            setLoading('ai', true);
            
            try {
                const response = await fetch('/api/query', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ sql_query: query, question: question })
                });
                const data = await response.json();
                
                if (response.ok) {
                    renderOutput(data.result);
                } else {
                    showError(data.detail || "Query execution failed.");
                }
            } catch (err) {
                showError("Failed to invoke backend server.");
            } finally {
                setLoading('ai', false);
            }
        });

        // UI Loading control
        function setLoading(type, isLoading) {
            const spinner = document.getElementById(`${type}Spinner`);
            const btn = document.getElementById(
                type === 'sql' ? 'runSqlQueryBtn' : 
                type === 'translate' ? 'translateBtn' : 'runAiQueryBtn'
            );
            
            if (isLoading) {
                spinner.style.display = 'block';
                btn.disabled = true;
                btn.style.opacity = '0.6';
            } else {
                spinner.style.display = 'none';
                btn.disabled = false;
                btn.style.opacity = '1';
            }
        }

        // Render Results Markup
        function renderOutput(mcpMarkdownResult) {
            const container = document.getElementById('resultsContainer');
            const counterDiv = document.getElementById('resultCount');
            container.innerHTML = '';
            counterDiv.textContent = '';
            
            // Extract record counts and download links via parsing markdown response
            const downloadMatch = mcpMarkdownResult.match(/\\[Click to Download Excel\\]\\((http:[^\\)]+)\\)/);
            const countMatch = mcpMarkdownResult.match(/\\*\\*Record Count\\*\\*:\\s*(\\d+)/);
            
            if (countMatch) {
                counterDiv.textContent = `${countMatch[1]} row(s) returned`;
            }
            
            // If download link exists, draw a beautiful card
            if (downloadMatch) {
                const downloadUrl = downloadMatch[1];
                const banner = document.createElement('div');
                banner.className = 'download-banner';
                banner.innerHTML = `
                    <div class="download-info">
                        <h4>BCG Styled Excel Report Generated</h4>
                        <p>File contains a Summary and custom-formatted Data sheet.</p>
                    </div>
                    <a href="${downloadUrl}" download class="btn btn-primary" style="text-decoration: none;">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                        Download Report (.xlsx)
                    </a>
                `;
                container.appendChild(banner);
            }
            
            // Parse and render the markdown table
            const lines = mcpMarkdownResult.split('\\n');
            let tableLines = [];
            let inTable = false;
            
            for (let line of lines) {
                if (line.trim().startsWith('|')) {
                    tableLines.push(line);
                    inTable = true;
                } else if (inTable) {
                    // Table ended
                    break;
                }
            }
            
            if (tableLines.length > 0) {
                const tableContainer = document.createElement('div');
                tableContainer.className = 'md-table-container';
                
                const table = document.createElement('table');
                
                // Parse headers
                const headers = tableLines[0].split('|').map(s => s.trim()).filter(s => s !== '');
                const thead = document.createElement('thead');
                const headerRow = document.createElement('tr');
                headers.forEach(h => {
                    const th = document.createElement('th');
                    th.textContent = h;
                    headerRow.appendChild(th);
                });
                thead.appendChild(headerRow);
                table.appendChild(thead);
                
                // Parse body rows (skip headers and divider line)
                const tbody = document.createElement('tbody');
                for (let i = 2; i < tableLines.length; i++) {
                    const rowData = tableLines[i].split('|').map(s => s.trim()).filter((s, idx, arr) => idx > 0 && idx < arr.length - 1);
                    if (rowData.length === 0) continue;
                    
                    const tr = document.createElement('tr');
                    rowData.forEach(d => {
                        const td = document.createElement('td');
                        td.textContent = d;
                        tr.appendChild(td);
                    });
                    tbody.appendChild(tr);
                }
                table.appendChild(tbody);
                tableContainer.appendChild(table);
                
                const previewTitle = document.createElement('h4');
                previewTitle.textContent = "Data Preview (First 5 Rows)";
                previewTitle.style.marginBottom = "0.75rem";
                previewTitle.style.marginTop = "1rem";
                
                container.appendChild(previewTitle);
                container.appendChild(tableContainer);
            } else {
                const empty = document.createElement('div');
                empty.className = 'empty-state';
                empty.innerHTML = `
                    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
                    <div>
                        <p style="font-weight: 500;">No Preview Table Available</p>
                        <p style="font-size: 0.85rem; color: var(--text-secondary);">Query executed but returned no visual grid columns.</p>
                    </div>
                `;
                container.appendChild(empty);
            }
        }

        // Run initial setup
        fetchDbSchema();
        updateUiTabLabels();
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    return HTML_CONTENT

if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "10016"))
    import uvicorn
    logger.info(f"Starting Dashboard Client on port {port}...")
    uvicorn.run("dashboard:app", host="0.0.0.0", port=port, reload=True)
