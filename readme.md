# MCP Insights: Go Server & Go Client Agent

A unified, high-performance implementation of the Unilever Model Context Protocol (MCP) stack. Both the core database MCP server and the intelligent LangChain agent client are written in **Go**, utilizing the latest stateless **Streamable HTTP** transport.

---

## Prerequisites

Ensure you have the following installed on your machine:
1. **Golang** (Go 1.22+ recommended)
2. **PostgreSQL** (running locally or remotely)
3. **Python 3.10+** (only required to run the custom `excel_generator.py` for BCG-styled spreadsheet reports)

---

## Project Structure

```
mcp_ia/
├── main.go               # Go MCP Server source code
├── go.mod / go.sum       # Go module and dependency lock files
├── .env                  # Port configs, database credentials & API keys
├── go_mock_ia/
│   └── main.go           # Go Agent Client Web Server & CLI runner
└── excel_generator.py    # Python report utility invoked by export_excel tool
```

---

## Configuration

Create or open the **`.env`** file in the root folder and configure your connections:
```env
# PostgreSQL Connection Configuration
PG_HOST=localhost
PG_PORT=5432
PG_USER=postgres
PG_PASSWORD=your_postgres_password
PG_DATABASE=your_database_name

# Server Settings
MCP_SERVER_PORT=10015
DASHBOARD_PORT=10016

# LLM Configuration (Azure OpenAI or Native OpenAI)
USE_AZURE=true
AZURE_OPENAI_API_KEY=your_key
AZURE_OPENAI_ENDPOINT=https://your-endpoint.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
AZURE_OPENAI_API_VERSION=2024-05-01-preview

# OR for Native OpenAI:
# USE_AZURE=false
# OPENAI_API_KEY=your_openai_key
# OPENAI_MODEL=gpt-4o
```

---

## Building the Executables

Because compiled binaries (`*.exe`) are excluded from Git version control, you need to compile them locally:

### 1. Build the Go MCP Server
```powershell
go build -o mcp_server.exe main.go
```

### 2. Build the Go Client Agent
```powershell
go build -o go_mock_ia.exe go_mock_ia/main.go
```

---

## Running the Application

### Step 1: Start the Go MCP Server
Start the stateless Go server in your first terminal:
```powershell
./mcp_server.exe
```
The server will bind to port `10015`, connect to PostgreSQL, and expose tools (`get_database_schema` and `query_postgres`) statelessly.

### Step 2: Run the Go Client Agent

You can run the compiled `go_mock_ia.exe` in two modes:

#### Option A: Web UI Dashboard (Default)
Run the client with no arguments to start the web dashboard server on port `10016`:
```powershell
./go_mock_ia.exe
```
* Open your browser to **[http://localhost:10016](http://localhost:10016)**.
* Ask natural language questions (e.g. *"Show me the top 2 rows of the projects table"*).
* Table schema references will load automatically in the left sidebar tree.
* Log messages, thinking tokens, and tool calls stream in real-time.

#### Option B: CLI Mode
Run the client and pass your question as command-line arguments to execute it in the terminal:
```powershell
./go_mock_ia.exe "give top 2 rows of projects table"
```
The agent will execute its thinking loop, call database tools statelessly, output tokens token-by-token, and print the final Markdown result.
