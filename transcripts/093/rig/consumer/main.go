// Command kairoconsumer is the CONSUMER-BOUNDARY leg. It is a real MCP client,
// built on the same SDK version Bifrost vendors
// (github.com/mark3labs/mcp-go v0.43.2), speaking the streamable-HTTP transport
// against a gateway's MCP endpoint.
//
// This is the class of client a Claude Desktop / Cursor / Claude Code style host
// runs: it connects over HTTP, initializes, lists tools, and calls one. It
// reports how an SDK consumer classifies the call it received, which is the
// question a byte-level assertion cannot answer on its own.
//
// usage: kairoconsumer <base-url> <tool-name>
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/mark3labs/mcp-go/client"
	"github.com/mark3labs/mcp-go/mcp"
)

func main() {
	if len(os.Args) < 3 {
		fmt.Fprintln(os.Stderr, "usage: kairoconsumer <base-url> <tool-name>")
		os.Exit(2)
	}
	baseURL := os.Args[1]
	tool := os.Args[2]

	mcpClient, err := client.NewStreamableHttpClient(baseURL)
	if err != nil {
		fmt.Fprintf(os.Stderr, "client: %v\n", err)
		os.Exit(1)
	}
	defer mcpClient.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	if _, err := mcpClient.Initialize(ctx, mcp.InitializeRequest{}); err != nil {
		fmt.Fprintf(os.Stderr, "initialize: %v\n", err)
		os.Exit(1)
	}

	listed, err := mcpClient.ListTools(ctx, mcp.ListToolsRequest{})
	if err != nil {
		fmt.Fprintf(os.Stderr, "list tools: %v\n", err)
		os.Exit(1)
	}
	names := make([]string, 0, len(listed.Tools))
	for _, entry := range listed.Tools {
		names = append(names, entry.Name)
	}

	result, err := mcpClient.CallTool(ctx, mcp.CallToolRequest{
		Params: mcp.CallToolParams{Name: tool, Arguments: map[string]any{}},
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "call %s: %v\n", tool, err)
		os.Exit(1)
	}

	raw, err := result.MarshalJSON()
	if err != nil {
		fmt.Fprintf(os.Stderr, "marshal result: %v\n", err)
		os.Exit(1)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		fmt.Fprintf(os.Stderr, "unmarshal result: %v\n", err)
		os.Exit(1)
	}
	_, present := fields["isError"]

	// How an SDK consumer reports the call. Every mcp-go consumer branch is on
	// the decoded bool; the absent key decodes to false, so it reads as success.
	classification := "SUCCESS"
	if result.IsError {
		classification = "FAILURE"
	}

	texts := make([]string, 0, len(result.Content))
	for _, block := range result.Content {
		if text, ok := block.(mcp.TextContent); ok {
			texts = append(texts, text.Text)
		}
	}

	out := map[string]any{
		"base_url":             baseURL,
		"tools_listed":         names,
		"tool":                 tool,
		"decoded_is_error":     result.IsError,
		"consumer_classifies":  classification,
		"wire_has_is_error":    present,
		"wire_result_verbatim": string(raw),
		"content_texts":        texts,
	}
	encoded, err := json.Marshal(out)
	if err != nil {
		fmt.Fprintf(os.Stderr, "marshal report: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("CONSUMER %s\n", encoded)
}
