#  MCP Insights: Go Server & Python Dashboard

A hybrid, high-performance implementation of the Unilever Model Context Protocol (MCP) stack. The core MCP server is implemented in compiled **Golang** using the official Go MCP SDK over the stateless **Streamable HTTP** transport. The interactive client interface is an interactive web dashboard written in **Python (FastAPI)**.

---

## Prerequisites

Ensure you have the following installed on your machine:
1.  **Golang** (Go 1.22+ recommended) - *Already installed at `C:\Program Files\Go`*
2.  **Python 3.10+** (with `pip`)
3.  **PostgreSQL** (running locally or remotely)

---

## Project Structure

```
mcp_ia/
├── main.go               # Go MCP Server source code
├── mcp_server.exe        # Compiled Go server executable
├── dashboard.py          # Python web dashboard client
├── .env                  # Port configs & PostgreSQL credentials
├── go.mod / go.sum       # Go module and dependency lock files
└── requirements.txt      # Python dependencies for the dashboard client
```

---

## Getting Started

### 1. Setup Python Virtual Environment
Open a terminal in the project directory and create a virtual environment to install the dashboard dependencies:
```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Create or open the **`.env`** file in the root folder and configure your Postgres database connection:
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

# Azure OpenAI Configuration (Optional, for Text-to-SQL translation)
AZURE_OPENAI_API_KEY=your_key
AZURE_OPENAI_ENDPOINT=https://your-endpoint.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
AZURE_OPENAI_API_VERSION=2024-05-01-preview
```

---

## Running the Application

To run the full stack, you need to launch the **Go MCP Server** first, followed by the **Python Dashboard Client**.

### Step 1: Run the Go MCP Server
Start the compiled Go server in your terminal:
```powershell
./mcp_server.exe
```
*(If compiling from source, run: `go build -o mcp_server.exe main.go` first)*

The Go server will:
*   Read connection credentials from `.env`.
*   Establish connection to PostgreSQL.
*   Expose tools (`get_database_schema` and `query_postgres`) over Streamable HTTP on port `10015`.

### Step 2: Run the Python Dashboard
Open a **new terminal tab/window**, activate the virtual environment, and start the dashboard client:
```powershell
.\.venv\Scripts\activate
python dashboard.py
```
*(The Dashboard runs on port **10016** by default)*

---

## Verifying and Working with the Dashboard

1.  Open your web browser and navigate to: **[http://localhost:10016](http://localhost:10016)**
2.  **DB Schema Explorer**:
    *   The sidebar on the left will automatically load your database tables and columns (fetched directly from the Go server's `/schema` endpoint).
3.  **Execute Queries**:
    *   **Direct SQL Tab**: Write any PostgreSQL query (e.g., `SELECT * FROM products LIMIT 5;`) and click **Run Query**. The results will be fetched by calling the Go MCP tool and rendered as a clean Markdown table preview.
    *   **AI Agent Tab (Natural Language)**:
        *   Ensure your Azure OpenAI keys are configured (either in `.env` or in the browser's top-right Configurations Gear Icon).
        *   Type a natural language question (e.g. *"how many products are there"*), click **Translate to SQL**, and execute!
