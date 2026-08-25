package main

import (
	"bufio"
	"context"
	"log"
	"os"
	"strings"

	"mcp_server/internal/orchestrator/agent"
	"mcp_server/internal/orchestrator/api"
	"mcp_server/internal/orchestrator/mcpclient"
)

// loadEnv reads the .env file and sets environment variables.
func loadEnv() {
	// Look in current directory or project root
	paths := []string{".env", "../../.env"}
	var file *os.File
	var err error
	for _, p := range paths {
		file, err = os.Open(p)
		if err == nil {
			break
		}
	}
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
			if (strings.HasPrefix(value, "\"") && strings.HasSuffix(value, "\"")) ||
				(strings.HasPrefix(value, "'") && strings.HasSuffix(value, "'")) {
				value = value[1 : len(value)-1]
			}
			os.Setenv(key, value)
		}
	}
}

func main() {
	loadEnv()
	log.Println("Starting Stateless Go LangChain Orchestrator...")

	// 1. Setup LLM
	useAzure := os.Getenv("USE_AZURE") == "true"
	llm, err := agent.SetupLLM(
		useAzure,
		os.Getenv("AZURE_OPENAI_ENDPOINT"),
		os.Getenv("AZURE_OPENAI_API_KEY"),
		os.Getenv("AZURE_OPENAI_API_VERSION"),
		os.Getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
		os.Getenv("OPENAI_MODEL"),
	)
	if err != nil {
		log.Fatalf("Failed to initialize LLM: %v", err)
	}

	// 2. Setup Stateless MCP Client
	mcpPort := os.Getenv("MCP_SERVER_PORT")
	if mcpPort == "" {
		mcpPort = "10015"
	}
	mcpURL := "http://localhost:" + mcpPort + "/mcp"

	clientManager := mcpclient.NewClientManager(mcpURL)
	ctx := context.Background()

	if err := clientManager.Connect(ctx); err != nil {
		log.Fatalf("Failed to connect to MCP Server at %s: %v", mcpURL, err)
	}

	// 3. Start Subscriptions (Tools, Prompts, Resources)
	clientManager.ListenForSubscriptions(ctx, func() {
		log.Println("Tools or capabilities updated dynamically from server!")
		// Automatically fetch the new tools so they are ready for the next query
		if err := clientManager.Discover(ctx); err != nil {
			log.Printf("Error during dynamic re-discovery: %v", err)
		} else {
			log.Printf("Successfully re-discovered tools: %d tools now available.", len(clientManager.Tools))
		}
	})

	// 4. Start REST API
	sysPrompt := `You are a smart insights AI database agent. 
You must use the provided tools to query databases and answer the user. 
IMPORTANT: When you use a tool that returns a Markdown table or data preview (like query_postgres), you MUST copy and paste the EXACT Markdown table, including all headers, rows, and the **Record Count**, directly into your final answer. Do NOT summarize or reformat the table, because the frontend UI relies on parsing that exact Markdown syntax to render the visual grid.`
	
	apiPort := os.Getenv("ORCHESTRATOR_PORT")
	if apiPort == "" {
		apiPort = "10016"
	}

	server := api.NewServer(clientManager, llm, sysPrompt, apiPort)
	if err := server.Start(); err != nil {
		log.Fatalf("API Server failed: %v", err)
	}
}
