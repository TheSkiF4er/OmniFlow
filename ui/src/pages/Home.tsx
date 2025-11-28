import React, { useEffect, useState } from 'react';
import { Row, Col } from 'antd';
import Dashboard from '../components/Dashboard';
import WorkflowViewer from '../components/WorkflowViewer';
import { Node, Edge } from 'react-flow-renderer';
import axios from 'axios';

const Home: React.FC = () => {
    const [workflowNodes, setWorkflowNodes] = useState<Node[]>([]);
    const [workflowEdges, setWorkflowEdges] = useState<Edge[]>([]);

    useEffect(() => {
        fetchWorkflowData();
    }, []);

    const fetchWorkflowData = async () => {
        try {
            const response = await axios.get('/api/workflows/latest'); // API endpoint
            const data = response.data;

            // Преобразуем данные в nodes и edges для React Flow
            setWorkflowNodes(data.nodes || []);
            setWorkflowEdges(data.edges || []);
        } catch (error) {
            console.error('Error fetching workflow data:', error);
        }
    };

    return (
        <div style={{ padding: '20px' }}>
            <Row gutter={[16, 16]}>
                <Col span={24}>
                    <Dashboard />
                </Col>
                <Col span={24}>
                    <WorkflowViewer nodes={workflowNodes} edges={workflowEdges} />
                </Col>
            </Row>
        </div>
    );
};

export default Home;
