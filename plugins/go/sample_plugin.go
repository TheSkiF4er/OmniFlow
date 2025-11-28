package main

import (
	"encoding/json"
	"fmt"
	"os"
)

// Event represents input data for the plugin
type Event struct {
	Data map[string]interface{} `json:"data"`
}

// Result represents output data from the plugin
type Result struct {
	Message string                 `json:"message"`
	Output  map[string]interface{} `json:"output"`
}

func main() {
	// Read JSON input from stdin
	var event Event
	decoder := json.NewDecoder(os.Stdin)
	if err := decoder.Decode(&event); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to decode input: %v\n", err)
		os.Exit(1)
	}

	// Process the event (example: multiply a number by 2)
	output := make(map[string]interface{})
	if val, ok := event.Data["number"].(float64); ok {
		output["result"] = val * 2
	} else {
		output["result"] = nil
	}

	// Prepare result
	res := Result{
		Message: "Go plugin executed successfully!",
		Output:  output,
	}

	// Write JSON output to stdout
	encoder := json.NewEncoder(os.Stdout)
	if err := encoder.Encode(res); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to encode result: %v\n", err)
		os.Exit(1)
	}
}
