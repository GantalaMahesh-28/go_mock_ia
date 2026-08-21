package api

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"

	"github.com/tmc/langchaingo/agents"
	"github.com/tmc/langchaingo/chains"
	"github.com/tmc/langchaingo/llms"
	"mcp_server/internal/orchestrator/agent"
	"mcp_server/internal/orchestrator/mcpclient"
)

type Server struct {
	ClientManager *mcpclient.ClientManager
	LLM           llms.Model
	SystemPrompt  string
	Port          string
}

func NewServer(cm *mcpclient.ClientManager, llm llms.Model, sysPrompt, port string) *Server {
	return &Server{
		ClientManager: cm,
		LLM:           llm,
		SystemPrompt:  sysPrompt,
		Port:          port,
	}
}

func (s *Server) Start() error {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /", s.handleRoot)
	mux.HandleFunc("GET /api/schema", s.handleSchema)
	mux.HandleFunc("GET /stream", s.handleStream)

	addr := ":" + s.Port
	log.Printf("Orchestrator REST API serving at http://localhost%s/", addr)
	return http.ListenAndServe(addr, mux)
}

func (s *Server) handleRoot(w http.ResponseWriter, r *http.Request) {
	// Look for the HTML file in the project root (where it was created)
	// Since we run go run main.go inside cmd/orchestrator, it's 2 levels up
	http.ServeFile(w, r, "../../orchestrator_ui.html")
}

func (s *Server) handleSchema(w http.ResponseWriter, r *http.Request) {
	// For simplicity, proxy to the MCP server's schema endpoint if available, or build locally.
	// We'll just return a mock response here to demonstrate the standard library routing.
	w.Header().Set("Content-Type", "application/json")
	w.Write([]byte(`{"schema": "Schema data should be queried via the get_database_schema tool"}`))
}

func (s *Server) handleStream(w http.ResponseWriter, r *http.Request) {
	question := r.URL.Query().Get("question")
	if question == "" {
		w.WriteHeader(http.StatusBadRequest)
		w.Write([]byte("Missing question query parameter"))
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "Streaming unsupported", http.StatusInternalServerError)
		return
	}

	writeSSE := func(eventType string, data any) {
		b, _ := json.Marshal(data)
		fmt.Fprintf(w, "event: %s\ndata: %s\n\n", eventType, string(b))
		flusher.Flush()
	}

	writeSSE("info", "Agent workspace initialized.")

	// Dynamically wrap the latest discovered MCP tools into LangChain tools
	lcTools := mcpclient.GenerateLangChainTools(s.ClientManager.Session, s.ClientManager.Tools)

	cb := &agent.StreamCallback{
		OnLLMToken: func(token string) {
			writeSSE("token", token)
		},
		OnToolStart: func(input string) {
			writeSSE("tool_start", input)
		},
		OnToolEnd: func(output string) {
			writeSSE("tool_end", output)
		},
	}

	customAgent := &agent.OpenAIAgent{
		LLM:              s.LLM,
		Tools:            lcTools,
		OutputKey:        "output",
		CallbacksHandler: cb,
		SystemPrompt:     s.SystemPrompt,
	}

	executor := agents.NewExecutor(
		customAgent,
		agents.WithMaxIterations(5),
		agents.WithReturnIntermediateSteps(),
		agents.WithParserErrorHandler(agents.NewParserErrorHandler(func(e string) string {
			return fmt.Sprintf("Parsing error: %s", e)
		})),
	)

	log.Printf("Executing query stream for UI: %q", question)
	res, err := executor.Call(r.Context(), map[string]any{
		"input": question,
	}, chains.WithCallback(cb))

	if err != nil {
		log.Printf("Agent Execution Error: %v", err)
		writeSSE("error", fmt.Sprintf("Execution error: %v", err))
	} else {
		log.Printf("Agent Execution Successful.")
		writeSSE("final_answer", res["output"])
	}
}
