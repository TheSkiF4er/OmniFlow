import React, { useState, useCallback } from 'react';
import ReactFlow, {
    ReactFlowProvider,
    addEdge,
    MiniMap,
    Controls,
    Background,
    Connection,
    Edge,
    Node,
} from 'react-flow-renderer';
import { Button } from 'antd';

const initialNodes: Node[] = [
    {
        id: '1',
        type: 'input',
        data: { label: 'Start' },
        position: { x: 250, y: 5 },
    },
];

const initialEdges: Edge[] = [];

const WorkflowEditor: React.FC = () => {
    const [nodes, setNodes] = useState<Node[]>(initialNodes);
    const [edges, setEdges] = useState<Edge[]>(initialEdges);

    const onConnect = useCallback(
        (params: Edge | Connection) => setEdges((eds) => addEdge(params, eds)),
        []
    );

    const addTaskNode = () => {
        const id = (nodes.length + 1).toString();
        const newNode: Node = {
            id,
            data: { label: `Task ${id}` },
            position: { x: Math.random() * 400, y: Math.random() * 400 },
        };
        setNodes((nds) => nds.concat(newNode));
    };

    return (
        <div style={{ height: 600, border: '1px solid #ddd', borderRadius: 4, padding: 10 }}>
            <h2>Workflow Editor</h2>
            <Button type="primary" onClick={addTaskNode} style={{ marginBottom: 10 }}>
                Add Task
            </Button>
            <ReactFlowProvider>
                <ReactFlow
                    nodes={nodes}
                    edges={edges}
                    onNodesChange={setNodes}
                    onEdgesChange={setEdges}
                    onConnect={onConnect}
                    fitView
                >
                    <MiniMap />
                    <Controls />
                    <Background color="#aaa" gap={16} />
                </ReactFlow>
            </ReactFlowProvider>
        </div>
    );
};

export default WorkflowEditor;
