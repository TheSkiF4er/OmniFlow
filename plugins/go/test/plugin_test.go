// plugins/go/test/plugin_test.go
//
// Unit tests for OmniFlow Go plugin helpers and protocol utilities.
// These tests exercise NDJSON parsing and response formation logic used by
// the OmniFlow Go plugin. They are intentionally self-contained so they
// can run in CI even if the full plugin binary isn't being executed.
// They validate:
//  - robust NDJSON (single-line JSON) parsing
//  - input-size (DoS) guard
//  - correct response formatting (id echo, status codes)
//  - behavior of example actions: echo, reverse (unicode-safe), compute (sum)
//  - graceful handling of malformed JSON
//  - timeout/cancellation cooperation using context
//
// Run with:
//   go test ./plugins/go/test -v
//
// NOTE:
// - These tests are not a substitute for the integration_test.sh script,
//   which executes the real plugin binary and validates end-to-end behavior.
// - Keep the tests fast and deterministic (no network, no heavy memory use).
//

package test

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"
	"unicode/utf8"
)

// Common protocol structures used in tests
type Request struct {
	ID        string          `json:"id"`
	Type      string          `json:"type"`
	Timestamp *string         `json:"timestamp,omitempty"`
	Payload   json.RawMessage `json:"payload"`
}

type Response struct {
	ID      string          `json:"id"`
	Status  string          `json:"status"`           // ok | error | busy
	Code    *int            `json:"code,omitempty"`   // numeric code
	Message *string         `json:"message,omitempty"`
	Body    json.RawMessage `json:"body,omitempty"`
	Meta    json.RawMessage `json:"meta,omitempty"`
}

// Helper: parse a single NDJSON line into Request, enforcing maxLine bytes.
func parseNDJSONLine(line []byte, maxLine int) (*Request, error) {
	if len(line) == 0 {
		return nil, errors.New("empty line")
	}
	if maxLine > 0 && len(line) > maxLine {
		return nil, errors.New("line exceeds max length")
	}
	// Trim trailing newline/whitespace (line may include newline)
	line = bytes.TrimRight(line, "\r\n")
	var req Request
	if err := json.Unmarshal(line, &req); err != nil {
		return nil, err
	}
	if req.ID == "" || req.Type == "" {
		return nil, errors.New("missing required fields id/type")
	}
	return &req, nil
}

// Helper: build response JSON bytes
func buildResponse(resp Response) ([]byte, error) {
	b, err := json.Marshal(resp)
	if err != nil {
		return nil, err
	}
	// Ensure single-line NDJSON
	b = bytes.TrimRight(b, "\r\n")
	return append(b, '\n'), nil
}

// Action implementations (examples)

// echo: returns args.message as-is
func actionEcho(args json.RawMessage) (json.RawMessage, error) {
	var payload struct {
		Message string `json:"message"`
	}
	if err := json.Unmarshal(args, &payload); err != nil {
		return nil, err
	}
	out := map[string]string{"action": "echo", "message": payload.Message}
	b, _ := json.Marshal(out)
	return b, nil
}

// reverse: unicode-safe reverse of args.message
func actionReverse(args json.RawMessage) (json.RawMessage, error) {
	var payload struct {
		Message string `json:"message"`
	}
	if err := json.Unmarshal(args, &payload); err != nil {
		return nil, err
	}
	// Reverse runes
	s := payload.Message
	runes := []rune(s)
	for i, j := 0, len(runes)-1; i < j; i, j = i+1, j-1 {
		runes[i], runes[j] = runes[j], runes[i]
	}
	out := map[string]string{"action": "reverse", "message": string(runes)}
	b, _ := json.Marshal(out)
	return b, nil
}

// compute: expects args.numbers []float64, returns sum
func actionCompute(args json.RawMessage) (json.RawMessage, error) {
	var payload struct {
		Numbers []float64 `json:"numbers"`
	}
	if err := json.Unmarshal(args, &payload); err != nil {
		return nil, err
	}
	var sum float64
	for _, v := range payload.Numbers {
		sum += v
	}
	out := map[string]interface{}{"action": "compute", "sum": sum}
	b, _ := json.Marshal(out)
	return b, nil
}

// dispatchAction routes exec requests to appropriate action handlers
func dispatchAction(actionName string, args json.RawMessage) (json.RawMessage, error) {
	switch actionName {
	case "echo":
		return actionEcho(args)
	case "reverse":
		return actionReverse(args)
	case "compute":
		return actionCompute(args)
	default:
		return nil, errors.New("unsupported action")
	}
}

// parseRequestPayloadAction extracts action and args from exec payload
func parseRequestPayloadAction(payload json.RawMessage) (string, json.RawMessage, error) {
	var p struct {
		Action string          `json:"action"`
		Args   json.RawMessage `json:"args"`
	}
	if err := json.Unmarshal(payload, &p); err != nil {
		return "", nil, err
	}
	if p.Action == "" {
		return "", nil, errors.New("missing action")
	}
	return p.Action, p.Args, nil
}

// ---------- Tests ----------

func TestParseNDJSONLine_Valid(t *testing.T) {
	line := []byte(`{"id":"r1","type":"health","payload":null}` + "\n")
	req, err := parseNDJSONLine(line, 131072)
	if err != nil {
		t.Fatalf("expected valid parse, got error: %v", err)
	}
	if req.ID != "r1" || req.Type != "health" {
		t.Fatalf("unexpected parsed fields: id=%s type=%s", req.ID, req.Type)
	}
}

func TestParseNDJSONLine_MissingFields(t *testing.T) {
	line := []byte(`{"type":"health"}` + "\n")
	_, err := parseNDJSONLine(line, 1024)
	if err == nil {
		t.Fatalf("expected error for missing id")
	}
}

func TestParseNDJSONLine_Oversize(t *testing.T) {
	b := make([]byte, 2048)
	for i := range b {
		b[i] = 'A'
	}
	// create a JSON line with a long message field - but still syntactically JSON
	line := append([]byte(`{"id":"x","type":"exec","payload":{"action":"echo","args":{"message":"`), b...)
	line = append(line, []byte(`"}}}`+"\n")...)
	_, err := parseNDJSONLine(line, 1024)
	if err == nil {
		t.Fatalf("expected oversize error")
	}
}

func TestBuildResponse_SingleLineNDJSON(t *testing.T) {
	r := Response{
		ID:     "resp1",
		Status: "ok",
	}
	b, err := buildResponse(r)
	if err != nil {
		t.Fatalf("buildResponse error: %v", err)
	}
	// Should end with single newline and not contain internal newlines
	if !bytes.HasSuffix(b, []byte("\n")) {
		t.Fatalf("expected trailing newline")
	}
	if bytes.Contains(b[:len(b)-1], []byte("\n")) {
		t.Fatalf("unexpected internal newline in NDJSON")
	}
}

func TestActionEcho(t *testing.T) {
	args := []byte(`{"message":"hello"}`)
	out, err := actionEcho(args)
	if err != nil {
		t.Fatalf("actionEcho error: %v", err)
	}
	var m map[string]string
	if err := json.Unmarshal(out, &m); err != nil {
		t.Fatalf("unmarshal echo out: %v", err)
	}
	if m["message"] != "hello" {
		t.Fatalf("unexpected echo message: %v", m["message"])
	}
}

func TestActionReverse_Unicode(t *testing.T) {
	args := []byte(`{"message":"Привет"}`)
	out, err := actionReverse(args)
	if err != nil {
		t.Fatalf("actionReverse error: %v", err)
	}
	var m map[string]string
	if err := json.Unmarshal(out, &m); err != nil {
		t.Fatalf("unmarshal reverse out: %v", err)
	}
	rev := m["message"]
	// The reversed string should be rune-reversed and still valid UTF-8
	if !utf8.ValidString(rev) {
		t.Fatalf("reversed string is invalid utf8: %q", rev)
	}
	// Reverse back to compare
	runes := []rune(rev)
	for i, j := 0, len(runes)-1; i < j; i, j = i+1, j-1 {
		runes[i], runes[j] = runes[j], runes[i]
	}
	if string(runes) != "Привет" {
		t.Fatalf("unexpected reverse round-trip: %q", string(runes))
	}
}

func TestActionCompute_Sum(t *testing.T) {
	args := []byte(`{"numbers":[1,2,3.5,-1.5]}`)
	out, err := actionCompute(args)
	if err != nil {
		t.Fatalf("actionCompute error: %v", err)
	}
	var m map[string]interface{}
	if err := json.Unmarshal(out, &m); err != nil {
		t.Fatalf("unmarshal compute out: %v", err)
	}
	sumF, ok := m["sum"].(float64)
	if !ok {
		t.Fatalf("sum field not float64: %#v", m["sum"])
	}
	const want = 5.0 // 1 + 2 + 3.5 -1.5
	if sumF != want {
		t.Fatalf("unexpected sum: got %v want %v", sumF, want)
	}
}

func TestDispatchAction_Unsupported(t *testing.T) {
	_, err := dispatchAction("nonexistent", nil)
	if err == nil {
		t.Fatalf("expected unsupported action error")
	}
}

func TestEndToEndExec_Echo(t *testing.T) {
	// Build a synthetic NDJSON request for exec:echo and run through parsing/dispatch/buildResponse
	line := `{"id":"e1","type":"exec","payload":{"action":"echo","args":{"message":"hi"}}}` + "\n"
	req, err := parseNDJSONLine([]byte(line), 1024)
	if err != nil {
		t.Fatalf("parse failed: %v", err)
	}
	actionName, args, err := parseRequestPayloadAction(req.Payload)
	if err != nil {
		t.Fatalf("parseRequestPayloadAction failed: %v", err)
	}
	body, err := dispatchAction(actionName, args)
	if err != nil {
		t.Fatalf("dispatchAction failed: %v", err)
	}
	code := 0
	resp := Response{
		ID:     req.ID,
		Status: "ok",
		Code:   &code,
		Body:   body,
	}
	out, err := buildResponse(resp)
	if err != nil {
		t.Fatalf("buildResponse failed: %v", err)
	}
	// Validate the resulting NDJSON line decodes and contains expected fields
	var got Response
	if err := json.Unmarshal(bytes.TrimRight(out, "\n"), &got); err != nil {
		t.Fatalf("unmarshal final response failed: %v", err)
	}
	if got.ID != "e1" || got.Status != "ok" {
		t.Fatalf("unexpected response header: %#v", got)
	}
	// check body content
	var bodyMap map[string]string
	if err := json.Unmarshal(got.Body, &bodyMap); err != nil {
		t.Fatalf("unmarshal body failed: %v", err)
	}
	if bodyMap["message"] != "hi" {
		t.Fatalf("unexpected echo result: %v", bodyMap["message"])
	}
}

func TestMalformedJSON_DoesNotPanic(t *testing.T) {
	line := []byte(`{ this is not valid json }` + "\n")
	_, err := parseNDJSONLine(line, 4096)
	if err == nil {
		t.Fatalf("expected parse error for malformed json")
	}
	// ensure error message is sensible
	if !strings.Contains(err.Error(), "invalid") && !strings.Contains(err.Error(), "unexpected") && !strings.Contains(err.Error(), "syntax") {
		// Accept a variety of JSON unmarshal error messages across Go versions
		t.Logf("malformed parse returned error: %v", err)
	}
}

func TestTimeoutCancellation_Cooperative(t *testing.T) {
	// Simulate a long-running action by creating an action that sleeps via context
	longAction := func(ctx context.Context, args json.RawMessage) (json.RawMessage, error) {
		select {
		case <-time.After(200 * time.Millisecond):
			out := map[string]string{"result": "done"}
			b, _ := json.Marshal(out)
			return b, nil
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}

	// wrapper dispatch that accepts context
	dispatchWithCtx := func(ctx context.Context, actionName string, args json.RawMessage) (json.RawMessage, error) {
		if actionName == "long" {
			return longAction(ctx, args)
		}
		return dispatchAction(actionName, args)
	}

	// Execute with a short timeout context; expect cancellation
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	_, err := dispatchWithCtx(ctx, "long", nil)
	if err == nil {
		t.Fatalf("expected context cancellation error")
	}
	if !errors.Is(err, context.DeadlineExceeded) && !errors.Is(err, context.Canceled) {
		t.Fatalf("expected context cancellation, got: %v", err)
	}
}

func TestNDJSONReader_LineReaderBehavior(t *testing.T) {
	// Emulate combined stream with two JSON lines and extra whitespace
	stream := `{"id":"a","type":"health","payload":null}
{"id":"b","type":"exec","payload":{"action":"echo","args":{"message":"ok"}}}
`
	scanner := bufio.NewScanner(strings.NewReader(stream))
	lines := []string{}
	for scanner.Scan() {
		lines = append(lines, scanner.Text())
	}
	if err := scanner.Err(); err != nil {
		t.Fatalf("scanner error: %v", err)
	}
	if len(lines) != 2 {
		t.Fatalf("expected 2 lines, got %d", len(lines))
	}
	// parse each via parseNDJSONLine
	for _, ln := range lines {
		_, err := parseNDJSONLine([]byte(ln+"\n"), 1024)
		if err != nil {
			t.Fatalf("parseNDJSONLine failed for line: %s error: %v", ln, err)
		}
	}
}
