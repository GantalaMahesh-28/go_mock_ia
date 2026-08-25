package mcpclient

import (
	"context"
	"fmt"
	"log"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// ClientManager handles the stateless MCP connection, discovery, and subscriptions.
type ClientManager struct {
	Client    *mcp.Client
	Session   *mcp.ClientSession
	ServerURL string

	// Store discovered capabilities
	Tools     []*mcp.Tool
	Prompts   []*mcp.Prompt
	Resources []*mcp.Resource
	
	// Native callback reference
	OnUpdate func()
}

// NewClientManager initializes the stateless MCP client transport.
func NewClientManager(serverURL string) *ClientManager {
	return &ClientManager{
		ServerURL: serverURL,
	}
}

// Connect establishes the stateless session and performs discovery.
func (cm *ClientManager) Connect(ctx context.Context) error {
	log.Printf("Connecting to Stateless MCP Server at %s...", cm.ServerURL)

	transport := &mcp.StreamableClientTransport{
		Endpoint:             cm.ServerURL,
		DisableStandaloneSSE: true,
	}
	
	// Create client using the native SDK Unified Subscription handlers!
	client := mcp.NewClient(&mcp.Implementation{
		Name:    "Go-Orchestrator-Client",
		Version: "1.0.0",
	}, &mcp.ClientOptions{
		ToolListChangedHandler: func(ctx context.Context, req *mcp.ToolListChangedRequest) {
			log.Println("🔔 MCP Notification Received natively: toolsListChanged")
			if cm.OnUpdate != nil {
				cm.OnUpdate()
			}
		},
	})

	session, err := client.Connect(ctx, transport, nil)
	if err != nil {
		return fmt.Errorf("failed to connect to MCP Server: %w", err)
	}
	
	cm.Client = client
	cm.Session = session

	// Perform discovery
	if err := cm.Discover(ctx); err != nil {
		return fmt.Errorf("server discovery failed: %w", err)
	}

	return nil
}

// Discover fetches capabilities dynamically.
func (cm *ClientManager) Discover(ctx context.Context) error {
	log.Println("Discovering MCP Server capabilities...")

	toolsResult, err := cm.Session.ListTools(ctx, nil)
	if err != nil {
		return err
	}
	cm.Tools = toolsResult.Tools

	if promptsResult, err := cm.Session.ListPrompts(ctx, nil); err == nil {
		cm.Prompts = promptsResult.Prompts
	}
	if resourcesResult, err := cm.Session.ListResources(ctx, nil); err == nil {
		cm.Resources = resourcesResult.Resources
	}

	log.Printf("Discovered %d tools, %d prompts, %d resources", len(cm.Tools), len(cm.Prompts), len(cm.Resources))
	return nil
}

// ListenForSubscriptions sets the callback. The actual SDK handles the multiplexing natively.
func (cm *ClientManager) ListenForSubscriptions(ctx context.Context, onUpdate func()) {
	cm.OnUpdate = onUpdate
	log.Println("Successfully subscribed to real-time multiplexed notifications (Native SDK Mode)!")
}
