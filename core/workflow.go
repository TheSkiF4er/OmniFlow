package core

import (
	"context"
	"fmt"
	"log"
)

// WorkflowNodeType defines type of node
type WorkflowNodeType string

const (
	NodeTypeTask     WorkflowNodeType = "task"
	NodeTypeDecision WorkflowNodeType = "decision"
	NodeTypeStart    WorkflowNodeType = "start"
	NodeTypeEnd      WorkflowNodeType = "end"
)

// WorkflowNode represents a single step or node in a workflow
type WorkflowNode struct {
	ID        string
	Name      string
	Type      WorkflowNodeType
	Plugin    Plugin
	Next      []*WorkflowNode
	Condition func(event Event) bool
}

// Workflow defines a complete workflow
type Workflow struct {
	ID        string
	Name      string
	Description string
	StartNode *WorkflowNode
	Nodes     map[string]*WorkflowNode
}

// AddNode adds a node to the workflow
func (wf *Workflow) AddNode(node *WorkflowNode) {
	if wf.Nodes == nil {
		wf.Nodes = make(map[string]*WorkflowNode)
	}
	wf.Nodes[node.ID] = node
	if node.Type == NodeTypeStart {
		wf.StartNode = node
	}
}

// ConnectNodes creates a link from parent to child node
func (wf *Workflow) ConnectNodes(parentID, childID string) error {
	parent, ok := wf.Nodes[parentID]
	if !ok {
		return fmt.Errorf("parent node %s not found", parentID)
	}
	child, ok := wf.Nodes[childID]
	if !ok {
		return fmt.Errorf("child node %s not found", childID)
	}
	parent.Next = append(parent.Next, child)
	return nil
}

// Execute runs the workflow starting from StartNode
func (wf *Workflow) Execute(ctx context.Context, engine *Engine, event Event) {
	if wf.StartNode == nil {
		log.Println("No start node defined for workflow", wf.Name)
		return
	}
	engine.executeNode(wf.StartNode, event)
}

// ExampleWorkflow creates a sample workflow for demonstration
func ExampleWorkflow() *Workflow {
	start := &WorkflowNode{
		ID:   "start",
		Name: "Start Node",
		Type: NodeTypeStart,
		Plugin: &PrintPlugin{
			NameStr: "StartPrinter",
		},
	}

	task1 := &WorkflowNode{
		ID:   "task1",
		Name: "Task Node 1",
		Type: NodeTypeTask,
		Plugin: &PrintPlugin{
			NameStr: "TaskPrinter1",
		},
	}

	task2 := &WorkflowNode{
		ID:   "task2",
		Name: "Task Node 2",
		Type: NodeTypeTask,
		Plugin: &PrintPlugin{
			NameStr: "TaskPrinter2",
		},
	}

	end := &WorkflowNode{
		ID:   "end",
		Name: "End Node",
		Type: NodeTypeEnd,
		Plugin: &PrintPlugin{
			NameStr: "EndPrinter",
		},
	}

	wf := &Workflow{
		ID:   "wf1",
		Name: "Example Workflow",
		Description: "Demonstration workflow for OmniFlow",
	}

	wf.AddNode(start)
	wf.AddNode(task1)
	wf.AddNode(task2)
	wf.AddNode(end)

	wf.ConnectNodes("start", "task1")
	wf.ConnectNodes("task1", "task2")
	wf.ConnectNodes("task2", "end")

	return wf
}
