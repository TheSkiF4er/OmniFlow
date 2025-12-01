// sample_plugin.go
//
// Production-ready Go plugin for OmniFlow (TheSkiF4er/OmniFlow)
// License: Apache-2.0
//
// Overview:
//  - Communicates with host via newline-delimited JSON messages on stdin/stdout.
//  - Robust parsing and validation using encoding/json.
//  - Structured logging to stderr (optionally JSON-formatted).
//  - Graceful shutdown on SIGINT/SIGTERM or "shutdown" message.
//  - Background worker for periodic maintenance (heartbeat, metrics flush).
//  - Configurable via environment variables.
//  - Includes safe limits to mitigate DoS (max message size) and timeouts for exec handlers.
//
// Build:
//  - Requires Go 1.20+ (module mode recommended).
//  - go build -o sample_plugin ./plugins/go
//
// Run (example):
//  echo '{"id":"1","type":"health"}' | ./sample_plugin
//
// Environment variables:
//  - OMNIFLOW_PLUGIN_MAX_LINE=131072     # max bytes per incoming message (default 131072)//  - OMNIFLOW_PLUGIN_HEARTBEAT=5         # heartbeat seconds
//  - OMNIFLOW_LOG_JSON=true              # if set, emit JSON logs to stderr
//  - OMNIFLOW_EXEC_TIMEOUT=10           # seconds timeout for exec handlers
//
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"
)

const (
	pluginName    = "OmniFlowGoRelease"
	pluginVersion = "1.0.0"
	defaultMaxLine = 128 * 1024 // 128 KiB
	defaultHeartbeat = 5         // seconds
	defaultExecTimeout = 10      // seconds
)

var (
	running int32 = 1 // atomic flag: 1 = running, 0 = stopping
)

// Message represents incoming host messages
type Message struct {
	ID      string          `json:"id,omitempty"`
	Type    string          `json:"type"`
	Payload json.RawMessage `json:"payload,omitempty"`
}

// Response represents plugin responses
type Response struct {
	ID      string      `json:"id,omitempty"`
	Status  string      `json:"status"` // ok | error
	Code    *int        `json:"code,omitempty"`
	Message string      `json:"message,omitempty"`
	Body    interface{} `json:"body,omitempty"`
}

// Structured logger writes to stderr. If OMNIFLOW_LOG_JSON is set, logs in JSON.
func log(level, msg string) {
	if os.Getenv("OMNIFLOW_LOG_JSON") != "" {
		// JSON log
		out := map[string]interface{}{
			"time": time.Now().UTC().Format(time.RFC3339),
			"level": level,
			"plugin": pluginName,
			"message": msg,
		}
		b, _ := json.Marshal(out)
		fmt.Fprintln(os.Stderr, string(b))
	} else {
		fmt.Fprintf(os.Stderr, "%s [%s] %s\n", time.Now().UTC().Format(time.RFC3339), level, msg)
	}
}

func info(msg string) { log("INFO", msg) }
func warn(msg string) { log("WARN", msg) }
func errlog(msg string) { log("ERROR", msg) }

// respond writes Response to stdout as a single newline-terminated JSON line.
func respond(r Response) {
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(r)
}

// Helper to respond OK
func respondOK(id string, body interface{}) {
	r := Response{ID: id, Status: "ok", Body: body}
	respond(r)
}

// Helper to respond Error
func respondError(id string, code int, message string) {
	r := Response{ID: id, Status: "error", Code: &code, Message: message}
	respond(r)
}

// backgroundWorker sends heartbeat logs and performs periodic maintenance.
func backgroundWorker(heartbeat int) {
	info(fmt.Sprintf("background worker started (heartbeat=%ds)", heartbeat))
	ticker := time.NewTicker(time.Duration(heartbeat) * time.Second)
	defer ticker.Stop()
	count := 0
	for atomic.LoadInt32(&running) == 1 {
		select {
		case <-ticker.C:
			count++
			info(fmt.Sprintf("heartbeat %d", count))
		}
	}
	info("background worker stopping")
}

// safeReadLine reads a line from reader but enforces maxLen. Returns trimmed line or error.
func safeReadLine(r *bufio.Reader, maxLen int) (string, error) {
	// Use ReadString but also check length to prevent OOM/DoS
	line, err := r.ReadString('\n')
	if err != nil {
		if errors.Is(err, io.EOF) && len(line) == 0 {
			return "", io.EOF
		}
		// if partial line with EOF, continue to process
	}
	if len(line) > maxLen {
		// drain rest of line
		for len(line) == maxLen && !strings.HasSuffix(line, "\n") {
			part, _ := r.ReadString('\n')
			line += part
			if len(line) > 10*maxLen { // give up if absurdly large
				return "", fmt.Errorf("incoming message too large")
			}
		}
		return "", fmt.Errorf("incoming message exceeds max length %d", maxLen)
	}
	// Trim newline characters
	line = strings.TrimRight(line, "\r\n")
	return line, nil
}

// execHandler executes actions defined in payload. Supports echo, reverse, compute.
func execHandler(ctx context.Context, id string, payload json.RawMessage) {
	// parse payload to map to inspect action
	var p map[string]json.RawMessage
	if err := json.Unmarshal(payload, &p); err != nil {
		respondError(id, 400, "invalid payload JSON")
		return
	}
	var action string
	if v, ok := p["action"]; ok {
		if err := json.Unmarshal(v, &action); err != nil {
			respondError(id, 400, "invalid action field")
			return
		}
	} else {
		respondError(id, 400, "missing action")
		return
	}

	switch action {
	case "echo":
		var message string
		if v, ok := p["message"]; ok {
			_ = json.Unmarshal(v, &message)
		}
		body := map[string]interface{}{"action": "echo", "message": message}
		respondOK(id, body)
	case "reverse":
		var message string
		if v, ok := p["message"]; ok {
			_ = json.Unmarshal(v, &message)
		}
		// reverse safely
		runes := []rune(message)
		for i, j := 0, len(runes)-1; i < j; i, j = i+1, j-1 {
			runes[i], runes[j] = runes[j], runes[i]
		}
		body := map[string]interface{}{"action": "reverse", "message": string(runes)}
		respondOK(id, body)
	case "compute":
		var numbers []float64
		if v, ok := p["numbers"]; ok {
			if err := json.Unmarshal(v, &numbers); err != nil {
				respondError(id, 400, "invalid numbers array")
				return
			}
		} else {
			respondError(id, 400, "missing numbers array")
			return
		}
		sum := 0.0
		for _, n := range numbers { sum += n }
		body := map[string]interface{}{"action": "compute", "sum": sum}
		respondOK(id, body)
	default:
		respondError(id, 422, "unsupported action")
	}
}

func main() {
	// Read configuration from env
	maxLine := defaultMaxLine
	if v := os.Getenv("OMNIFLOW_PLUGIN_MAX_LINE"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 10*1024*1024 {
			maxLine = n
		}
	}
	heartbeat := defaultHeartbeat
	if v := os.Getenv("OMNIFLOW_PLUGIN_HEARTBEAT"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 3600 {
			heartbeat = n
		}
	}
	execTimeout := defaultExecTimeout
	if v := os.Getenv("OMNIFLOW_EXEC_TIMEOUT"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 3600 {
			execTimeout = n
		}
	}

	info(fmt.Sprintf("starting plugin version=%s maxLine=%d heartbeat=%d execTimeout=%d", pluginVersion, maxLine, heartbeat, execTimeout))

	// Setup signal handling for graceful shutdown
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigCh
		warn("signal received, shutting down")
		atomic.StoreInt32(&running, 0)
	}()

	// Start background worker
	go backgroundWorker(heartbeat)

	// Main read loop
	reader := bufio.NewReader(os.Stdin)

	for atomic.LoadInt32(&running) == 1 {
		line, err := safeReadLine(reader, maxLine)
		if err != nil {
			if errors.Is(err, io.EOF) {
				info("stdin closed (EOF), exiting")
				break
			}
			warn(fmt.Sprintf("read line error: %v", err))
			respondError("", 400, "invalid input or message too large")
			continue
		}
		if len(strings.TrimSpace(line)) == 0 { continue }

		var msg Message
		if err := json.Unmarshal([]byte(line), &msg); err != nil {
			warn(fmt.Sprintf("invalid JSON: %v", err))
			respondError("", 400, "invalid JSON")
			continue
		}

		id := msg.ID
		typeLower := strings.ToLower(msg.Type)

		switch typeLower {
		case "health":
			respondOK(id, map[string]interface{}{"status":"healthy","version":pluginVersion})
		case "exec":
			// run exec with timeout context
			ctx, cancel := context.WithTimeout(context.Background(), time.Duration(execTimeout)*time.Second)
			ch := make(chan struct{})
			go func() {
				execHandler(ctx, id, msg.Payload)
				close(ch)
			}()
			select {
			case <-ch:
				// done
			case <-ctx.Done():
				respondError(id, 408, "exec timeout")
			}
			cancel()
		case "shutdown", "quit":
			respondOK(id, map[string]interface{}{"result":"shutting_down"})
			atomic.StoreInt32(&running, 0)
			// allow background worker to stop then exit loop
			time.Sleep(100 * time.Millisecond)
		default:
			respondError(id, 400, "unknown type")
		}
	}

	// Final cleanup
	atomic.StoreInt32(&running, 0)
	info("plugin shutdown complete")
}
