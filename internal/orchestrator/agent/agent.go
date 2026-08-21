package agent

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/tmc/langchaingo/agents"
	"github.com/tmc/langchaingo/callbacks"
	"github.com/tmc/langchaingo/chains"
	"github.com/tmc/langchaingo/llms"
	"github.com/tmc/langchaingo/llms/openai"
	"github.com/tmc/langchaingo/schema"
	"github.com/tmc/langchaingo/tools"
)

// StreamCallback handles LangChain events to support SSE streaming
type StreamCallback struct {
	callbacks.SimpleHandler
	OnLLMToken  func(token string)
	OnLLMEnd    func(res *llms.ContentResponse)
	OnToolStart func(input string)
	OnToolEnd   func(output string)
}

func (s *StreamCallback) HandleStreamingFunc(ctx context.Context, chunk []byte) {
	if s.OnLLMToken != nil {
		s.OnLLMToken(string(chunk))
	}
}

func (s *StreamCallback) HandleLLMGenerateContentEnd(ctx context.Context, res *llms.ContentResponse) {
	if s.OnLLMEnd != nil {
		s.OnLLMEnd(res)
	}
}

func (s *StreamCallback) HandleToolStart(ctx context.Context, input string) {
	if s.OnToolStart != nil {
		s.OnToolStart(input)
	}
}

func (s *StreamCallback) HandleToolEnd(ctx context.Context, output string) {
	if s.OnToolEnd != nil {
		s.OnToolEnd(output)
	}
}

// OpenAIAgent handles structured tool calling with Azure OpenAI / Native OpenAI.
type OpenAIAgent struct {
	LLM              llms.Model
	Tools            []tools.Tool
	OutputKey        string
	CallbacksHandler callbacks.Handler
	SystemPrompt     string
}

var _ agents.Agent = (*OpenAIAgent)(nil)

func (a *OpenAIAgent) GetInputKeys() []string  { return []string{"input"} }
func (a *OpenAIAgent) GetOutputKeys() []string { return []string{a.OutputKey} }
func (a *OpenAIAgent) GetTools() []tools.Tool  { return a.Tools }

func (a *OpenAIAgent) Plan(
	ctx context.Context,
	intermediateSteps []schema.AgentStep,
	inputs map[string]string,
	options ...chains.ChainCallOption,
) ([]schema.AgentAction, *schema.AgentFinish, error) {
	var messages []llms.ChatMessage

	// 1. System Prompt
	messages = append(messages, llms.SystemChatMessage{Content: a.SystemPrompt})

	// 2. Human Message
	messages = append(messages, llms.HumanChatMessage{Content: inputs["input"]})

	// 3. Scratchpad (Intermediate steps)
	scratchPad := a.constructScratchPad(intermediateSteps)
	messages = append(messages, scratchPad...)

	// Convert chat messages to ContentPart slice
	mcList := make([]llms.MessageContent, len(messages))
	for i, msg := range messages {
		role := msg.GetType()
		text := msg.GetContent()

		var mc llms.MessageContent
		switch p := msg.(type) {
		case llms.ToolChatMessage:
			mc = llms.MessageContent{
				Role: role,
				Parts: []llms.ContentPart{llms.ToolCallResponse{
					ToolCallID: p.ID,
					Content:    p.Content,
				}},
			}
		case llms.AIChatMessage:
			if len(p.ToolCalls) > 0 {
				toolCallParts := make([]llms.ContentPart, 0, len(p.ToolCalls))
				for _, tc := range p.ToolCalls {
					toolCallParts = append(toolCallParts, llms.ToolCall{
						ID:           tc.ID,
						Type:         tc.Type,
						FunctionCall: tc.FunctionCall,
					})
				}
				mc = llms.MessageContent{
					Role:  role,
					Parts: toolCallParts,
				}
			} else {
				mc = llms.MessageContent{
					Role:  role,
					Parts: []llms.ContentPart{llms.TextContent{Text: text}},
				}
			}
		default:
			mc = llms.MessageContent{
				Role:  role,
				Parts: []llms.ContentPart{llms.TextContent{Text: text}},
			}
		}
		mcList[i] = mc
	}

	var stream func(ctx context.Context, chunk []byte) error
	if a.CallbacksHandler != nil {
		stream = func(ctx context.Context, chunk []byte) error {
			a.CallbacksHandler.HandleStreamingFunc(ctx, chunk)
			return nil
		}
	}

	callOpts := []llms.CallOption{
		llms.WithStreamingFunc(stream),
	}

	funcs := a.functions()
	if len(funcs) > 0 {
		callOpts = append(callOpts, llms.WithFunctions(funcs))
	}

	res, err := a.LLM.GenerateContent(ctx, mcList, callOpts...)
	if err != nil {
		return nil, nil, err
	}

	return a.parseOutput(res)
}

type SchemaProvider interface {
	Schema() interface{}
}

func (a *OpenAIAgent) functions() []llms.FunctionDefinition {
	// Dynamically map from LangChain tools back to LLM function schemas
	var funcs []llms.FunctionDefinition
	for _, t := range a.Tools {
		var params interface{}
		if sp, ok := t.(SchemaProvider); ok && sp.Schema() != nil {
			params = sp.Schema()
		} else {
			// Fallback schema if missing
			params = map[string]any{
				"type": "object",
				"properties": map[string]any{
					"sql_query": map[string]any{
						"type":        "string",
						"description": "SQL Query (if applicable)",
					},
					"question": map[string]any{
						"type":        "string",
						"description": "Question (if applicable)",
					},
				},
			}
		}

		funcs = append(funcs, llms.FunctionDefinition{
			Name:        t.Name(),
			Description: t.Description(),
			Parameters:  params,
		})
	}
	return funcs
}

func (a *OpenAIAgent) constructScratchPad(steps []schema.AgentStep) []llms.ChatMessage {
	if len(steps) == 0 {
		return nil
	}

	var messages []llms.ChatMessage
	var currentToolCalls []llms.ToolCall
	var currentLog string

	for i, step := range steps {
		if i == 0 || step.Action.Log != steps[i-1].Action.Log {
			if len(currentToolCalls) > 0 {
				messages = append(messages, llms.AIChatMessage{
					Content:   currentLog,
					ToolCalls: currentToolCalls,
				})
				for j := i - len(currentToolCalls); j < i; j++ {
					messages = append(messages, llms.ToolChatMessage{
						ID:      steps[j].Action.ToolID,
						Content: steps[j].Observation,
					})
				}
				currentToolCalls = nil
			}
			currentLog = step.Action.Log
		}

		currentToolCalls = append(currentToolCalls, llms.ToolCall{
			ID:   step.Action.ToolID,
			Type: "function",
			FunctionCall: &llms.FunctionCall{
				Name:      step.Action.Tool,
				Arguments: step.Action.ToolInput,
			},
		})
	}

	if len(currentToolCalls) > 0 {
		messages = append(messages, llms.AIChatMessage{
			Content:   currentLog,
			ToolCalls: currentToolCalls,
		})
		for j := len(steps) - len(currentToolCalls); j < len(steps); j++ {
			messages = append(messages, llms.ToolChatMessage{
				ID:      steps[j].Action.ToolID,
				Content: steps[j].Observation,
			})
		}
	}

	return messages
}

func (a *OpenAIAgent) parseOutput(contentResp *llms.ContentResponse) ([]schema.AgentAction, *schema.AgentFinish, error) {
	if contentResp == nil || len(contentResp.Choices) == 0 {
		return nil, nil, fmt.Errorf("no choices in response")
	}
	choice := contentResp.Choices[0]

	if len(choice.ToolCalls) > 0 {
		actions := make([]schema.AgentAction, 0, len(choice.ToolCalls))
		turnPrefix := fmt.Sprintf("[%d] ", time.Now().UnixNano())
		for _, toolCall := range choice.ToolCalls {
			functionName := toolCall.FunctionCall.Name
			toolInputStr := toolCall.FunctionCall.Arguments

			actions = append(actions, schema.AgentAction{
				Tool:      functionName,
				ToolInput: toolInputStr,
				Log:       turnPrefix + fmt.Sprintf("Invoking: %s", functionName),
				ToolID:    toolCall.ID,
			})
		}
		return actions, nil, nil
	}

	return nil, &schema.AgentFinish{
		ReturnValues: map[string]any{
			"output": choice.Content,
		},
		Log: choice.Content,
	}, nil
}

// SetupLLM configures Azure OpenAI or Native OpenAI based on credentials.
func SetupLLM(useAzure bool, endpoint, apiKey, apiVersion, deployment, model string) (llms.Model, error) {
	if useAzure {
		log.Println("Initializing Chat Model: Azure OpenAI Integration")
		return openai.New(
			openai.WithAPIType(openai.APITypeAzure),
			openai.WithBaseURL(endpoint),
			openai.WithToken(apiKey),
			openai.WithAPIVersion(apiVersion),
			openai.WithModel(deployment),
		)
	}
	log.Println("Initializing Chat Model: Native OpenAI Integration")
	return openai.New(
		openai.WithToken(apiKey),
		openai.WithModel(model),
	)
}
