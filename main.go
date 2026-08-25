package main

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"

	_ "github.com/lib/pq"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// Global Configuration
var (
	DB        *sql.DB
	ExportDir = "./data/excel_exports"
)

func main() {
	log.Println("Starting Go MCP Server...")

	// 1. Load Environment Variables
	loadEnv()

	// 2. Connect to PostgreSQL
	dbConnStr := fmt.Sprintf("host=%s port=%s user=%s password=%s dbname=%s sslmode=disable",
		getEnv("PG_HOST", "localhost"),
		getEnv("PG_PORT", "5432"),
		getEnv("PG_USER", "postgres"),
		getEnv("PG_PASSWORD", ""),
		getEnv("PG_DATABASE", "postgres"),
	)
	
	var err error
	DB, err = sql.Open("postgres", dbConnStr)
	if err != nil {
		log.Fatalf("Failed to open database connection: %v", err)
	}
	defer DB.Close()

	// Ping database
	if err = DB.Ping(); err != nil {
		log.Printf("WARNING: Database connection failed: %v. Will retry during execution.", err)
	} else {
		log.Println("Connected successfully to PostgreSQL database.")
	}

	// Create export directory
	if err = os.MkdirAll(ExportDir, 0755); err != nil {
		log.Fatalf("Failed to create export directory: %v", err)
	}

	// 3. Initialize official Go MCP Server
	server := mcp.NewServer(&mcp.Implementation{
		Name:    "UL-Insights-Go-Server",
		Version: "1.0.0",
	}, nil)

	// 4. Register MCP Tools
	mcp.AddTool(server, &mcp.Tool{
		Name:        "get_database_schema",
		Description: "Queries database information schema and returns a structured string reference.",
	}, getDatabaseSchemaTool)

	mcp.AddTool(server, &mcp.Tool{
		Name:        "query_postgres",
		Description: "Executes a SQL query on PostgreSQL, returns a preview, and generates a formatted BCG Excel sheet.",
	}, queryPostgresTool)

	// 5. Create Streamable HTTP Handler
	mcpHandler := mcp.NewStreamableHTTPHandler(func(req *http.Request) *mcp.Server {
		return server
	}, &mcp.StreamableHTTPOptions{
		Stateless:                  true,
		DisableLocalhostProtection: true,
	})

	// 6. Setup Standard HTTP Routes
	mux := http.NewServeMux()

	// Health Endpoint
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		dbConnected := DB.Ping() == nil
		statusMsg := "Connected successfully"
		if !dbConnected {
			statusMsg = "Connection failed"
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status":             "healthy",
			"database_connected": dbConnected,
			"database_status":    statusMsg,
			"export_directory":   ExportDir,
		})
	})

	// Schema Endpoint (for sidebar UI Explorer)
	mux.HandleFunc("/schema", func(w http.ResponseWriter, r *http.Request) {
		schemaStr := getSchemaString("")
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{
			"schema": schemaStr,
		})
	})

	// File Server for Downloads
	mux.Handle("/download/", http.StripPrefix("/download/", http.FileServer(http.Dir(ExportDir))))

	// Manual Trigger to dynamically add a tool (PoC for dynamic subscriptions)
	mux.HandleFunc("/trigger-add-tool", func(w http.ResponseWriter, r *http.Request) {
		log.Println("Manual trigger received! Adding new mock tool dynamically...")
		mcp.AddTool(server, &mcp.Tool{
			Name:        "get_server_status",
			Description: "Returns the current health status of the MCP server.",
		}, func(ctx context.Context, req *mcp.CallToolRequest, input interface{}) (*mcp.CallToolResult, interface{}, error) {
			return &mcp.CallToolResult{
				Content: []mcp.Content{
					&mcp.TextContent{
						Text: "The server is running perfectly with dynamic subscriptions active!",
					},
				},
			}, nil, nil
		})

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{
			"status": "success",
			"message": "Tool 'get_server_status' added dynamically! Notification should be broadcast automatically by the SDK.",
		})
	})

	// Bind MCP Streamable HTTP handlers
	mux.Handle("/mcp", mcpHandler)
	mux.Handle("/mcp/", mcpHandler)

	// Start the Server
	port := getEnv("MCP_SERVER_PORT", "10015")
	log.Printf("Server listening on port %s...", port)
	
	serverAddr := ":" + port
	if err = http.ListenAndServe(serverAddr, mux); err != nil {
		log.Fatalf("HTTP server failed: %v", err)
	}
}

// ============================================================================
// Tool Registrations
// ============================================================================

type SchemaInput struct {
	TableName string `json:"table_name" jsonschema:"Optional specific table to inspect. If empty, returns summary of all tables."`
}
type SchemaOutput struct {
	Text string `json:"text"`
}

func getDatabaseSchemaTool(ctx context.Context, req *mcp.CallToolRequest, input SchemaInput) (*mcp.CallToolResult, interface{}, error) {
	schemaStr := getSchemaString(input.TableName)
	return &mcp.CallToolResult{
		Content: []mcp.Content{
			&mcp.TextContent{
				Text: schemaStr,
			},
		},
	}, nil, nil
}

type QueryInput struct {
	SQLQuery string `json:"sql_query" jsonschema:"The raw SQL query to run against the database."`
	Question string `json:"question" jsonschema:"The user's natural language question describing the query goal."`
}

type QueryOutput struct {
	Text string `json:"text"`
}

func queryPostgresTool(ctx context.Context, req *mcp.CallToolRequest, input QueryInput) (*mcp.CallToolResult, interface{}, error) {
	log.Printf("Executing query tool: %s", input.SQLQuery)

	if DB.Ping() != nil {
		return &mcp.CallToolResult{
			Content: []mcp.Content{
				&mcp.TextContent{
					Text: "Database Connection Error. Please ensure PostgreSQL is running.",
				},
			},
		}, nil, nil
	}

	// 1. Run PostgreSQL Query
	records, err := executePostgresQuery(input.SQLQuery)
	if err != nil {
		log.Printf("Error executing query: %v", err)
		return &mcp.CallToolResult{
			Content: []mcp.Content{
				&mcp.TextContent{
					Text: fmt.Sprintf("Error executing query: %v", err),
				},
			},
		}, nil, nil
	}

	// 2. Build Markdown Table Response
	var summaryLines []string
	summaryLines = append(summaryLines, "### Query Results Executed Successfully")
	summaryLines = append(summaryLines, fmt.Sprintf("**Record Count**: %d row(s)", len(records)))
	summaryLines = append(summaryLines, "")
	summaryLines = append(summaryLines, "#### Results Preview:")

	if len(records) == 0 {
		summaryLines = append(summaryLines, "*No data returned.*")
	} else {
		// Extract headers
		headers := getKeys(records[0])
		headerRow := "| " + strings.Join(headers, " | ") + " |"
		dividerRow := "| " + strings.Join(repeatString("---", len(headers)), " | ") + " |"
		summaryLines = append(summaryLines, headerRow)
		summaryLines = append(summaryLines, dividerRow)

		limit := 10
		if len(records) < 10 {
			limit = len(records)
		}

		for _, rec := range records[:limit] {
			var vals []string
			for _, h := range headers {
				val := rec[h]
				if val == nil {
					vals = append(vals, "")
				} else {
					vals = append(vals, fmt.Sprintf("%v", val))
				}
			}
			rowText := "| " + strings.Join(vals, " | ") + " |"
			summaryLines = append(summaryLines, rowText)
		}

		if len(records) > 10 {
			summaryLines = append(summaryLines, fmt.Sprintf("\n*...and %d more rows.*", len(records)-10))
		}
	}

	outputText := strings.Join(summaryLines, "\n")
	return &mcp.CallToolResult{
		Content: []mcp.Content{
			&mcp.TextContent{
				Text: outputText,
			},
		},
	}, nil, nil
}

// ============================================================================
// Database & Utility Helpers
// ============================================================================

func getSchemaString(filterTable string) string {
	query := `
		SELECT 
			table_name, 
			column_name, 
			data_type 
		FROM 
			information_schema.columns 
		WHERE 
			table_schema = 'public'
	`
	var args []interface{}
	if filterTable != "" {
		query += " AND table_name = $1"
		args = append(args, filterTable)
	}
	query += " ORDER BY table_name, ordinal_position;"

	rows, err := DB.Query(query, args...)
	if err != nil {
		return fmt.Sprintf("Error fetching schema: %v", err)
	}
	defer rows.Close()

	schemaMap := make(map[string][]string)
	for rows.Next() {
		var tableName, columnName, dataType string
		if err := rows.Scan(&tableName, &columnName, &dataType); err != nil {
			return fmt.Sprintf("Error reading schema rows: %v", err)
		}
		schemaMap[tableName] = append(schemaMap[tableName], fmt.Sprintf("%s (%s)", columnName, dataType))
	}

	if len(schemaMap) == 0 {
		return "No tables found in the public schema of the database."
	}

	var builder strings.Builder
	builder.WriteString("Database Schema Reference:")
	for table, cols := range schemaMap {
		builder.WriteString(fmt.Sprintf("\n\nTable: %s", table))
		for _, col := range cols {
			builder.WriteString(fmt.Sprintf("\n  - %s", col))
		}
	}
	return builder.String()
}

func executePostgresQuery(sqlQuery string) ([]map[string]interface{}, error) {
	rows, err := DB.Query(sqlQuery)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	cols, err := rows.Columns()
	if err != nil {
		return nil, err
	}

	var records []map[string]interface{}

	for rows.Next() {
		columns := make([]interface{}, len(cols))
		columnPointers := make([]interface{}, len(cols))
		for i := range columns {
			columnPointers[i] = &columns[i]
		}

		if err := rows.Scan(columnPointers...); err != nil {
			return nil, err
		}

		rowMap := make(map[string]interface{})
		for i, colName := range cols {
			val := columns[i]
			// Handle byte slices (strings/text/varchars are scanned as raw []byte slices)
			if b, ok := val.([]byte); ok {
				rowMap[colName] = string(b)
			} else {
				rowMap[colName] = val
			}
		}
		records = append(records, rowMap)
	}

	return records, nil
}



// ============================================================================
// Config & Helpers
// ============================================================================

func loadEnv() {
	file, err := os.Open(".env")
	if err != nil {
		log.Println("Note: .env file not found, using system environment variables.")
		return
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		if len(parts) == 2 {
			key := strings.TrimSpace(parts[0])
			value := strings.TrimSpace(parts[1])
			// If value has quotes, strip them
			if (strings.HasPrefix(value, "\"") && strings.HasSuffix(value, "\"")) ||
				(strings.HasPrefix(value, "'") && strings.HasSuffix(value, "'")) {
				value = value[1 : len(value)-1]
			}
			os.Setenv(key, value)
		}
	}
}

func getEnv(key, defaultVal string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultVal
}

func getKeys(m map[string]interface{}) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	return keys
}

func repeatString(s string, count int) []string {
	res := make([]string, count)
	for i := range res {
		res[i] = s
	}
	return res
}
