// Command kairodirect is the CONTROL-LEG client. It speaks MCP to a stdio MCP
// server directly, with no gateway in between, and reports what it received.
//
// Two views are printed for every call:
//
//   - the decoded CallToolResult struct, which is what an SDK consumer branches
//     on, and
//   - the raw JSON the SDK decodes that result from, so the wire assertion is
//     made against bytes rather than against a struct the same SDK produced.
//
// KEY PRESENCE is reported separately from the value. mcp-go omits isError when
// it is false (omitempty), so "absent" and "false" are the same wire fact and an
// assertion of value alone would pass vacuously.
//
// usage: kairodirect <command> <server-path> <tool-name> [ledger-path]
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
	if len(os.Args) < 4 {
		fmt.Fprintln(
			os.Stderr,
			"usage: kairodirect <command> <server-path> <tool-name> [ledger-path]",
		)
		os.Exit(2)
	}
	// command is what this client spawns. In the rig it is the transparent
	// kairotee relay, with server-path as its argument, so this leg records the
	// same upstream bytes the gateway leg records.
	command := os.Args[1]
	server := os.Args[2]
	tool := os.Args[3]
	if len(os.Args) > 4 && os.Args[4] != "" {
		if err := os.Setenv("KAIRO_LEDGER", os.Args[4]); err != nil {
			fmt.Fprintf(os.Stderr, "set ledger: %v\n", err)
			os.Exit(1)
		}
	}

	mcpClient, err := client.NewStdioMCPClient(command, nil, server)
	if err != nil {
		fmt.Fprintf(os.Stderr, "spawn: %v\n", err)
		os.Exit(1)
	}
	defer mcpClient.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	if _, err := mcpClient.Initialize(ctx, mcp.InitializeRequest{}); err != nil {
		fmt.Fprintf(os.Stderr, "initialize: %v\n", err)
		os.Exit(1)
	}

	result, err := mcpClient.CallTool(ctx, mcp.CallToolRequest{
		Params: mcp.CallToolParams{Name: tool, Arguments: map[string]any{}},
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "call %s: %v\n", tool, err)
		os.Exit(1)
	}

	// The bytes as the SDK's own encoder would put them back on the wire. This
	// is the same MarshalJSON that emits isError when true and omits it when
	// false, so the decoded struct and the wire form cannot disagree.
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

	// An SDK consumer that branches on the decoded field classifies the call as
	// failure only when IsError is true.
	classification := "SUCCESS"
	if result.IsError {
		classification = "FAILURE"
	}

	out := map[string]any{
		"tool":                 tool,
		"decoded_is_error":     result.IsError,
		"consumer_classifies":  classification,
		"wire_has_is_error":    present,
		"wire_is_error_value":  jsonValue(fields, "isError"),
		"wire_result_verbatim": string(raw),
	}
	encoded, err := json.Marshal(out)
	if err != nil {
		fmt.Fprintf(os.Stderr, "marshal report: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("DIRECT %s\n", encoded)
}

func jsonValue(fields map[string]json.RawMessage, key string) any {
	value, ok := fields[key]
	if !ok {
		return nil
	}
	var decoded any
	if err := json.Unmarshal(value, &decoded); err != nil {
		return string(value)
	}
	return decoded
}
