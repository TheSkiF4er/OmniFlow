package core

import (
	"context"
	"fmt"
	"log"
	"sync"
	"time"
)

// Event represents a single event in the workflow engine
type Event struct {
	ID      string
	Type    string
	Payload map[string]interface{}
}

// Plugin interface defines a standard plugin contract
type Plugin interface {
	Name() string
	Execute(ctx context.Context, event Event) (map[string]interface{}, error)
}

// WorkflowNode represents a node (step) in a workflow
type WorkflowNode struct {
	ID       string
	Name     string
	Plugin   Plugin
	Next     []*WorkflowNode
	Condition func(event Event) bool
}

// Workflow represents a workflow
type Workflow struct {
	ID    string
	Name  string
	Nodes map[string]*WorkflowNode
	Start *WorkflowNode
}

// Engine is the core workflow engine
type Engine struct {
	mu        sync.RWMutex
	workflows map[string]*Workflow
	eventChan chan Event
	ctx       context.Context
	cancel    context.CancelFunc
}

// NewEngine initializes a new workflow engine
func NewEngine(bufferSize int) *Engine {
	ctx, cancel := context.WithCancel(context.Background())
	return &Engine{
		workflows: make(map[string]*Workflow),
		eventChan: make(chan Event, bufferSize),
		ctx:       ctx,
		cancel:    cancel,
	}
}

// RegisterWorkflow adds a new workflow to the engine
func (e *Engine) RegisterWorkflow(wf *Workflow) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.workflows[wf.ID] = wf
	log.Printf("Workflow registered: %s\n", wf.Name)
}

// Emit sends an event to the engine for processing
func (e *Engine) Emit(event Event) {
	select {
	case e.eventChan <- event:
		log.Printf("Event emitted: %s\n", event.ID)
	case <-e.ctx.Done():
		log.Printf("Engine stopped, cannot emit event: %s\n", event.ID)
	}
}

// Start begins processing events
func (e *Engine) Start() {
	go func() {
		for {
			select {
			case event := <-e.eventChan:
				e.handleEvent(event)
			case <-e.ctx.Done():
				log.Println("Engine stopped")
				return
			}
		}
	}()
}

// Stop gracefully stops the engine
func (e *Engine) Stop() {
	e.cancel()
	close(e.eventChan)
	log.Println("Engine stopped gracefully")
}

// handleEvent dispatches events to matching workflows
func (e *Engine) handleEvent(event Event) {
	e.mu.RLock()
	defer e.mu.RUnlock()

	for _, wf := range e.workflows {
		if wf.Start != nil {
			go e.executeNode(wf.Start, event)
		}
	}
}

// executeNode executes a workflow node
func (e *Engine) executeNode(node *WorkflowNode, event Event) {
	if node.Condition != nil && !node.Condition(event) {
		log.Printf("Condition not met for node %s, skipping\n", node.Name)
		return
	}

	result, err := node.Plugin.Execute(e.ctx, event)
	if err != nil {
		log.Printf("Plugin execution failed for node %s: %v\n", node.Name, err)
		return
	}

	log.Printf("Node executed: %s, result: %v\n", node.Name, result)

	for _, nextNode := range node.Next {
		go e.executeNode(nextNode, Event{
			ID:      fmt.Sprintf("%s-next", event.ID),
			Type:    event.Type,
			Payload: result,
		})
	}
}

// Wait blocks until the engine is stopped
func (e *Engine) Wait() {
	<-e.ctx.Done()
}

// Example Plugin Implementation
type PrintPlugin struct {
	NameStr string
}

func (p *PrintPlugin) Name() string {
	return p.NameStr
}

func (p *PrintPlugin) Execute(ctx context.Context, event Event) (map[string]interface{}, error) {
	log.Printf("[PrintPlugin] Event: %v\n", event)
	return map[string]interface{}{"status": "printed"}, nil
}

// Example usage
func Example() {
	engine := NewEngine(100)
	engine.Start()

	printPlugin := &PrintPlugin{NameStr: "Printer"}
	node := &WorkflowNode{
		ID:     "node1",
		Name:   "Print Node",
		Plugin: printPlugin,
	}

	wf := &Workflow{
		ID:    "wf1",
		Name:  "Test Workflow",
		Nodes: map[string]*WorkflowNode{"node1": node},
		Start: node,
	}

	engine.RegisterWorkflow(wf)

	engine.Emit(Event{
		ID:      "evt1",
		Type:    "test",
		Payload: map[string]interface{}{"msg": "Hello OmniFlow"},
	})

	// Let workflow run for a short period
	time.Sleep(2 * time.Second)
	engine.Stop()
}
