// Copy this file to core/mcp/zz_h1_probe_test.go in the pinned Bifrost checkout.
package mcp

import (
	"strings"
	"testing"

	"github.com/maximhq/bifrost/core/schemas"
)

func h1String(value string) *string {
	return &value
}

func h1CallsAndResults() ([]schemas.ChatAssistantMessageToolCall, []*schemas.ChatMessage) {
	name := "KairoSideEffect-charge"
	firstID := "call-alpha"
	secondID := "call-beta"
	calls := []schemas.ChatAssistantMessageToolCall{
		{ID: &firstID, Function: schemas.ChatAssistantMessageToolCallFunction{Name: &name}},
		{ID: &secondID, Function: schemas.ChatAssistantMessageToolCallFunction{Name: &name}},
	}
	results := []*schemas.ChatMessage{
		{
			Role:    schemas.ChatMessageRoleTool,
			Content: &schemas.ChatMessageContent{ContentStr: h1String("KAIRO_H1_ALPHA")},
			ChatToolMessage: &schemas.ChatToolMessage{
				ToolCallID: &firstID,
			},
		},
		{
			Role:    schemas.ChatMessageRoleTool,
			Content: &schemas.ChatMessageContent{ContentStr: h1String("KAIRO_H1_BETA")},
			ChatToolMessage: &schemas.ChatToolMessage{
				ToolCallID: &secondID,
			},
		},
	}
	return calls, results
}

func h1AssertBothResults(t *testing.T, content string) {
	t.Helper()
	if !strings.Contains(content, "KAIRO_H1_ALPHA") || !strings.Contains(content, "KAIRO_H1_BETA") {
		t.Fatalf("two distinct call IDs executed, but response lost a result: %s", content)
	}
}

func TestH1InvariantChatKeepsBothSameNameResults(t *testing.T) {
	calls, results := h1CallsAndResults()
	response := createChatResponseWithExecutedToolsAndNonAutoExecutableCalls(
		&schemas.BifrostChatResponse{}, results, calls, nil,
	)
	content := response.Choices[0].ChatNonStreamResponseChoice.Message.Content.ContentStr
	if content == nil {
		t.Fatal("response has no result summary")
	}
	h1AssertBothResults(t, *content)
}

func TestH1InvariantResponsesKeepsBothSameNameResults(t *testing.T) {
	calls, results := h1CallsAndResults()
	response := createResponsesResponseWithExecutedToolsAndNonAutoExecutableCalls(
		&schemas.BifrostResponsesResponse{}, results, calls, nil,
	)
	if len(response.Output) != 1 || response.Output[0].Content == nil || len(response.Output[0].Content.ContentBlocks) != 1 {
		t.Fatal("response has no result summary")
	}
	content := response.Output[0].Content.ContentBlocks[0].Text
	if content == nil {
		t.Fatal("response summary has no text")
	}
	h1AssertBothResults(t, *content)
}
