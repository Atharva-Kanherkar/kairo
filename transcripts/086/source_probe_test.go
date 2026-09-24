package semanticcache

// Kairo issue 086 source-level probe. Copy into plugins/semanticcache of a
// Bifrost checkout and run:
//
//	GOWORK=off go test -run TestKairo086DirectHashIgnoresRequestFamily -count=1 -v .
//
// It builds a Chat Completions request and a Responses request with the same
// user text and asks the plugin for its direct-cache hash inputs. The test
// fails while the two families share one hash, which is the defect.

import (
	"testing"

	"github.com/maximhq/bifrost/core/schemas"
)

func kairo086Hash(t *testing.T, p *Plugin, req *schemas.BifrostRequest) (string, string) {
	t.Helper()
	metadata, err := p.buildRequestMetadataForCaching(nil, req)
	if err != nil {
		t.Fatalf("metadata: %v", err)
	}
	input, err := schemas.MarshalDeeplySorted(map[string]interface{}{
		"input":  p.getNormalizedInputForCaching(req),
		"params": metadata,
	})
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	hash, err := p.generateRequestHash(req, metadata)
	if err != nil {
		t.Fatalf("hash: %v", err)
	}
	return hash, string(input)
}

func TestKairo086DirectHashIgnoresRequestFamily(t *testing.T) {
	p := &Plugin{config: &Config{}}
	text := "hello"
	role := schemas.ResponsesInputMessageRoleUser
	messageType := schemas.ResponsesMessageTypeMessage

	chat := &schemas.BifrostRequest{
		RequestType: schemas.ChatCompletionRequest,
		ChatRequest: &schemas.BifrostChatRequest{Input: []schemas.ChatMessage{{
			Role:    schemas.ChatMessageRoleUser,
			Content: &schemas.ChatMessageContent{ContentStr: &text},
		}}},
	}
	// The shape the Responses handler builds from a string `input`.
	responses := &schemas.BifrostRequest{
		RequestType: schemas.ResponsesRequest,
		ResponsesRequest: &schemas.BifrostResponsesRequest{Input: []schemas.ResponsesMessage{{
			Role:    &role,
			Content: &schemas.ResponsesMessageContent{ContentStr: &text},
		}}},
	}
	typed := &schemas.BifrostRequest{
		RequestType: schemas.ResponsesRequest,
		ResponsesRequest: &schemas.BifrostResponsesRequest{Input: []schemas.ResponsesMessage{{
			Type:    &messageType,
			Role:    &role,
			Content: &schemas.ResponsesMessageContent{ContentStr: &text},
		}}},
	}

	chatHash, chatInput := kairo086Hash(t, p, chat)
	responsesHash, responsesInput := kairo086Hash(t, p, responses)
	typedHash, typedInput := kairo086Hash(t, p, typed)
	t.Logf("chat          hash=%s input=%s", chatHash, chatInput)
	t.Logf("responses     hash=%s input=%s", responsesHash, responsesInput)
	t.Logf("typed control hash=%s input=%s", typedHash, typedInput)

	if typedHash == chatHash {
		t.Fatalf("control: a typed Responses item should already hash differently")
	}
	if responsesHash == chatHash {
		t.Fatalf("defect: chat and Responses requests share direct-cache hash %s", chatHash)
	}
}
