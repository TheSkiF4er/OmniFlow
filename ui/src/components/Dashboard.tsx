import React, { useEffect, useState } from 'react';
import { Card, Row, Col, List, Button } from 'antd';
import axios from 'axios';

interface Workflow {
    id: string;
    name: string;
    status: string;
    lastRun: string;
}

const Dashboard: React.FC = () => {
    const [workflows, setWorkflows] = useState<Workflow[]>([]);
    const [loading, setLoading] = useState<boolean>(true);

    useEffect(() => {
        fetchWorkflows();
    }, []);

    const fetchWorkflows = async () => {
        try {
            const response = await axios.get('/api/workflows'); // API endpoint
            setWorkflows(response.data);
        } catch (error) {
            console.error('Error fetching workflows:', error);
        } finally {
            setLoading(false);
        }
    };

    const runWorkflow = async (id: string) => {
        try {
            await axios.post(`/api/workflows/${id}/run`);
            alert(`Workflow ${id} triggered successfully!`);
            fetchWorkflows();
        } catch (error) {
            console.error('Error running workflow:', error);
        }
    };

    return (
        <div style={{ padding: '24px' }}>
            <h1>OmniFlow Dashboard</h1>
            <Row gutter={16}>
                <Col span={24}>
                    <Card title="Workflows">
                        <List
                            loading={loading}
                            dataSource={workflows}
                            renderItem={workflow => (
                                <List.Item
                                    actions={[
                                        <Button
                                            type="primary"
                                            onClick={() => runWorkflow(workflow.id)}
                                        >
                                            Run
                                        </Button>
                                    ]}
                                >
                                    <List.Item.Meta
                                        title={workflow.name}
                                        description={`Status: ${workflow.status} | Last run: ${workflow.lastRun}`}
                                    />
                                </List.Item>
                            )}
                        />
                    </Card>
                </Col>
            </Row>
        </div>
    );
};

export default Dashboard;
