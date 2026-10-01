// Command kairosdkprobe establishes the SDK half of the attribution positively,
// with no Bifrost in the process.
//
// It prints, for each of the SDK's own result constructors and for one result
// built by hand, the exact bytes the SDK emits and what the SDK decodes back.
// This is the evidence that the SDK preserves the failure flag when a caller
// passes it one, and that an absent key decodes to false rather than to an
// error or to a missing value.
//
// It also decodes the two response bodies the reproduction recorded, one from a
// gateway that dropped the flag and one from a gateway that kept it, through the
// same decoder a consumer uses. That shows the byte difference is exactly the
// difference in the consumer's classification, with no other moving part.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/mark3labs/mcp-go/mcp"
)

func describe(label string, result *mcp.CallToolResult) {
	raw, err := result.MarshalJSON()
	if err != nil {
		fmt.Printf("SDK %s MARSHAL-ERROR %v\n", label, err)
		return
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		fmt.Printf("SDK %s UNMARSHAL-ERROR %v\n", label, err)
		return
	}
	_, present := fields["isError"]

	// Round-trip through the SDK's own UnmarshalJSON, the same path a client
	// takes when it decodes a gateway response.
	var back mcp.CallToolResult
	if err := back.UnmarshalJSON(raw); err != nil {
		fmt.Printf("SDK %s ROUNDTRIP-ERROR %v\n", label, err)
		return
	}
	fmt.Printf(
		"SDK %s bytes=%s key_present=%v decoded_is_error=%v\n",
		label, string(raw), present, back.IsError,
	)
}

// decode_as_consumer decodes a recorded tools/call `result` object the way a
// consumer does, and reports what a branch on the flag would conclude.
func decode_as_consumer(label, body string) {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal([]byte(body), &raw); err != nil {
		fmt.Printf("DECODE %s PARSE-ERROR %v\n", label, err)
		return
	}
	_, present := raw["isError"]
	var result mcp.CallToolResult
	if err := result.UnmarshalJSON([]byte(body)); err != nil {
		fmt.Printf("DECODE %s SDK-ERROR %v\n", label, err)
		return
	}
	classification := "SUCCESS"
	if result.IsError {
		classification = "FAILURE"
	}
	fmt.Printf(
		"DECODE %s key_present=%v decoded_is_error=%v consumer_would_say=%s\n",
		label, present, result.IsError, classification,
	)
}

func main() {
	text := mcp.NewToolResultText(`{"code":"KAIRO_OK","detail":"x"}`)
	failed := mcp.NewToolResultError(`{"code":"KAIRO_FAIL","detail":"x"}`)
	describe("NewToolResultText", text)
	describe("NewToolResultError", failed)

	// A hand-built result that sets the flag directly, which is what a caller
	// that understood the protocol would do.
	manual := &mcp.CallToolResult{
		Content: []mcp.Content{mcp.TextContent{Type: "text", Text: "x"}},
		IsError: true,
	}
	describe("HandBuiltIsErrorTrue", manual)

	for index, path := range os.Args[1:] {
		data, err := os.ReadFile(path)
		if err != nil {
			fmt.Printf("DECODE arg%d READ-ERROR %v\n", index+1, err)
			continue
		}
		decodeAsConsumerFile(fmt.Sprintf("arg%d", index+1), data)
	}
}

// looks_like_chunk_size reports whether the text starts with a valid hex chunk
// size, which distinguishes a chunked body from a whole-message one.
func looks_like_chunk_size(text string) bool {
	line := text
	if index := strings.Index(text, "\r\n"); index >= 0 {
		line = text[:index]
	}
	if line == "" {
		return false
	}
	for _, char := range line {
		isHex := (char >= '0' && char <= '9') || (char >= 'a' && char <= 'f') || (char >= 'A' && char <= 'F')
		if !isHex {
			return false
		}
	}
	return true
}

// decodeAsConsumerFile pulls the JSON-RPC `result` object out of a recorded
// tools/call response, whether or not it was written as a bare result.
func decodeAsConsumerFile(label string, data []byte) {
	text := strings.TrimSpace(string(data))
	// The recorded files are either a raw HTTP response (headers, blank line,
	// body) or a relay log with a direction tag. Both carry the body after the
	// first blank line.
	if index := strings.Index(text, "\r\n\r\n"); index >= 0 {
		text = text[index+4:]
	} else if index := strings.Index(text, "\n\n"); index >= 0 {
		text = text[index+2:]
	}
	text = strings.TrimSpace(text)
	// A chunked body starts with a hex size line; the JSON starts after it.
	if looks_like_chunk_size(text) {
		if index := strings.Index(text, "\r\n"); index >= 0 {
			text = text[index+2:]
		}
	}
	if strings.HasPrefix(text, "{") && strings.Contains(text, "\"result\"") {
		var envelope struct {
			Result json.RawMessage `json:"result"`
		}
		if err := json.Unmarshal([]byte(text), &envelope); err == nil && len(envelope.Result) > 0 {
			decode_as_consumer(label, string(envelope.Result))
			return
		}
	}
	decode_as_consumer(label, text)
}
