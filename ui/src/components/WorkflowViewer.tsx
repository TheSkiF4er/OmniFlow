import React from 'react';
import ReactFlow, { MiniMap, Controls, Background, Node, Edge } from 'react-flow-renderer';
import { Card } from 'antd';

interface WorkflowViewerProps {
    nodes: Node[];
    edges: Edge[];
}

const WorkflowViewer: React.FC<WorkflowViewerProps> = ({ nodes, edges }) => {
    return (
        <Card title="Workflow Viewer" style={{ margin: '20px 0', padding: '10px' }}>
            <div style={{ height: 500, border: '1px solid #ddd', borderRadius: 4 }}>
                <ReactFlow nodes={nodes} edges={edges} fitView>
                    <MiniMap />
                    <Controls />
                    <Background color="#aaa" gap={16} />
                </ReactFlow>
            </div>
        </Card>
    );
};

export default WorkflowViewer;
