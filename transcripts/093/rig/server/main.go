// Command kairoserver is the deterministic stdio MCP server behind every leg
// of the kairo isError investigation. It exposes exactly two tools:
//
//	kairofail-always_fails -> always a failure result, fixed payload
//	kairofail-always_ok     -> always a success result, fixed payload
//
// No clock, no randomness, no network, no environment-dependent behavior. The
// same call yields the same bytes on every run, which is what makes an N of N
// claim possible at all.
//
// The failing tool is built with mcp.NewToolResultError, the pinned SDK's own
// constructor for "the tool ran and failed". The success tool is built with
// mcp.NewToolResultText, the constructor for a normal result. The two handlers
// are otherwise identical, which is what makes the pair a single-variable
// control rather than two different tools.
//
// The payloads contain neither the substring "error" nor a leading "Error: ", so
// no downstream reader can infer failure from the string alone. The failure has
// to travel on the isError key.
//
// KAIRO_LEDGER names a file. Every actual tool execution appends one JSON Lines
// record there, so execution is counted where it happens rather than inferred
// from a transcript.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"sync"

	"github.com/mark3labs/mcp-go/mcp"
	"github.com/mark3labs/mcp-go/server"
)

const (
	failTool = "kairofail-always_fails"
	okTool   = "kairofail-always_ok"

	// Fixed payloads. Deliberately machine-shaped so an assertion does not have
	// to parse prose, and deliberately free of any word a reader could mistake
	// for a failure signal.
	failPayload = `{"code":"KAIRO_FAIL","detail":"deterministic upstream failure"}`
	okPayload   = `{"code":"KAIRO_OK","detail":"deterministic upstream success"}`
)

var ledgerMu sync.Mutex

// ledgerPath returns the ledger file named by the environment, or "" when unset.
// Direct stdio spawns set it; Bifrost's stdio_config envs set it for the
// gateway leg.
func ledgerPath() string {
	return os.Getenv("KAIRO_LEDGER")
}

// record appends one execution record. Failures to record are fatal: an
// unrecorded execution would make the ledger an unreliable count.
func record(tool string, resultIsError bool) {
	path := ledgerPath()
	if path == "" {
		return
	}
	ledgerMu.Lock()
	defer ledgerMu.Unlock()
	file, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		fmt.Fprintf(os.Stderr, "kairo ledger open: %v\n", err)
		os.Exit(1)
	}
	defer file.Close()
	line, err := json.Marshal(map[string]any{
		"tool":          tool,
		"returned_flag": resultIsError,
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "kairo ledger marshal: %v\n", err)
		os.Exit(1)
	}
	if _, err := file.Write(append(line, '\n')); err != nil {
		fmt.Fprintf(os.Stderr, "kairo ledger write: %v\n", err)
		os.Exit(1)
	}
}

func main() {
	s := server.NewMCPServer("kairo-fail-server", "1.0.0", server.WithToolCapabilities(true))

	// The failing tool. NewToolResultError is the SDK constructor for a tool
	// that ran and failed: CallToolResult{Content: [text], IsError: true}. It
	// returns no Go error, so nothing about the transport call looks wrong.
	s.AddTool(mcp.Tool{
		Name:        failTool,
		Description: "always fails",
		InputSchema: mcp.ToolInputSchema{Type: "object", Properties: map[string]any{}},
	}, func(context.Context, mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		record(failTool, true)
		return mcp.NewToolResultError(failPayload), nil
	})

	// The succeeding control tool. Identical shape, flag unset.
	s.AddTool(mcp.Tool{
		Name:        okTool,
		Description: "always succeeds",
		InputSchema: mcp.ToolInputSchema{Type: "object", Properties: map[string]any{}},
	}, func(context.Context, mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		record(okTool, false)
		return mcp.NewToolResultText(okPayload), nil
	})

	if err := server.ServeStdio(s); err != nil {
		fmt.Fprintf(os.Stderr, "kairo stdio server: %v\n", err)
		os.Exit(1)
	}
}
