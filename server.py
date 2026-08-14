import os
import logging
from contextlib import asynccontextmanager
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastmcp import FastMCP
import uvicorn

from excel_generator import generate_excel

# Load environment variables
load_dotenv()

# Logging config
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ul_mcp_server")

# Database configuration
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "postgres")
PG_DATABASE = os.getenv("PG_DATABASE", "postgres")
EXPORT_DIR = os.path.abspath("./data/excel_exports")
os.makedirs(EXPORT_DIR, exist_ok=True)

# 1. Initialize FastMCP
mcp = FastMCP("UL Insights MCP Server")

def get_db_connection():
    """Establishes and returns a connection to the PostgreSQL database."""
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASSWORD,
        dbname=PG_DATABASE
    )

def test_db_connection():
    """Simple test connection to see if DB is accessible."""
    try:
        conn = get_db_connection()
        conn.close()
        return True, "Connected successfully"
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return False, str(e)

# 2. Register MCP Tools
@mcp.tool()
def get_database_schema() -> str:
    """
    Retrieves the public database schema (tables, columns, and data types) 
    to understand what tables and columns are available to query.
    """
    is_ok, err_msg = test_db_connection()
    if not is_ok:
        return f"Database Connection Error: {err_msg}. Please check your database settings."
        
    query = """
    SELECT 
        table_name, 
        column_name, 
        data_type 
    FROM 
        information_schema.columns 
    WHERE 
        table_schema = 'public'
    ORDER BY 
        table_name, ordinal_position;
    """
    
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
        conn.close()
        
        if not rows:
            return "No tables found in the public schema of the database."
            
        schema_dict = {}
        for table_name, col_name, data_type in rows:
            if table_name not in schema_dict:
                schema_dict[table_name] = []
            schema_dict[table_name].append(f"{col_name} ({data_type})")
            
        result = ["Database Schema Reference:"]
        for table, cols in schema_dict.items():
            result.append(f"\nTable: {table}")
            for col in cols:
                result.append(f"  - {col}")
        return "\n".join(result)
        
    except Exception as e:
        return f"Error fetching schema: {e}"

@mcp.tool()
def query_postgres(sql_query: str, question: str) -> str:
    """
    Executes a SQL query on PostgreSQL, returns a preview, and generates a formatted BCG Excel sheet.
    
    Args:
        sql_query: The raw SQL query to run against the database.
        question: The user's natural language question describing the query goal.
    """
    is_ok, err_msg = test_db_connection()
    if not is_ok:
        return f"Database Connection Error: {err_msg}. Please ensure your Postgres database is running."
        
    logger.info(f"Executing query: {sql_query}")
    try:
        conn = get_db_connection()
        # Use RealDictCursor to map column names to values
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql_query)
            records = cur.fetchall()
        conn.close()
        
        # Standardize records: convert real dict structures to standard dicts
        standard_records = [dict(r) for r in records]
        
        # Generate Excel workbook
        filename = generate_excel(standard_records, question, sql_query, EXPORT_DIR)
        
        # Create server download link
        server_port = os.getenv("MCP_SERVER_PORT", "10015")
        download_url = f"http://localhost:{server_port}/download/{filename}"
        
        # Build Markdown response
        summary_lines = [
            f"### Query Results Executed Successfully",
            f"**Record Count**: {len(records)} row(s)",
            f"**Download BCG Excel Report**: [Click to Download Excel]({download_url})",
            "",
            "#### Preview (First 5 rows):"
        ]
        
        if not standard_records:
            summary_lines.append("*No data returned.*")
        else:
            # Render a markdown table for the first 5 records
            headers = list(standard_records[0].keys())
            header_row = "| " + " | ".join(headers) + " |"
            divider_row = "| " + " | ".join(["---"] * len(headers)) + " |"
            summary_lines.append(header_row)
            summary_lines.append(divider_row)
            
            for rec in standard_records[:5]:
                vals = [str(rec[h]) if rec[h] is not None else "" for h in headers]
                row_text = "| " + " | ".join(vals) + " |"
                summary_lines.append(row_text)
                
            if len(standard_records) > 5:
                summary_lines.append(f"\n*...and {len(standard_records) - 5} more rows.*")
                
        return "\n".join(summary_lines)
        
    except Exception as e:
        logger.error(f"Error querying database: {e}")
        return f"Error executing query: {e}"

# 3. Create FastAPI integration
mcp_app = mcp.http_app(path="/")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Test DB on start
    is_ok, msg = test_db_connection()
    if is_ok:
        logger.info("Successfully verified database connection on startup.")
    else:
        logger.warning(f"Database not accessible on startup: {msg}. Will try again during tool execution.")
    
    async with mcp_app.lifespan(app):
        yield

app = FastAPI(lifespan=lifespan, title="UL MCP Server & Excel Generator")

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serves file downloads
@app.get("/download/{filename}")
def download_file(filename: str):
    filepath = os.path.join(EXPORT_DIR, filename)
    if os.path.exists(filepath) and os.path.isfile(filepath):
        # Return file response with spreadsheet mime type
        return FileResponse(
            filepath, 
            filename=filename, 
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    raise HTTPException(status_code=404, detail="Excel file not found")

# Health check / status endpoint
@app.get("/health")
def health():
    db_ok, db_msg = test_db_connection()
    return {
        "status": "healthy",
        "database_connected": db_ok,
        "database_status": db_msg,
        "export_directory": EXPORT_DIR
    }

# Directly serve database schema via simple endpoint for dashboard client
@app.get("/schema")
def schema():
    schema_info = get_database_schema()
    return {"schema": schema_info}

# Mount the MCP server ASGI application
app.mount("/mcp", mcp_app)

if __name__ == "__main__":
    port = int(os.getenv("MCP_SERVER_PORT", "10015"))
    logger.info(f"Starting server on port {port}...")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)
