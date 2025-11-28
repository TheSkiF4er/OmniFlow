import React, { useEffect, useState } from 'react';
import { Table, Input, Button, Space, message, Card } from 'antd';
import { SearchOutlined, ReloadOutlined } from '@ant-design/icons';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';

interface Workflow {
    id: string;
    name: string;
    description: string;
    lastModified: string;
    status: string;
}

const Workflows: React.FC = () => {
    const [workflows, setWorkflows] = useState<Workflow[]>([]);
    const [loading, setLoading] = useState<boolean>(false);
    const [searchText, setSearchText] = useState<string>('');
    const navigate = useNavigate();

    useEffect(() => {
        fetchWorkflows();
    }, []);

    const fetchWorkflows = async () => {
        setLoading(true);
        try {
            const response = await axios.get('/api/workflows');
            setWorkflows(response.data);
        } catch (error) {
            console.error('Error fetching workflows:', error);
            message.error('Failed to load workflows');
        } finally {
            setLoading(false);
        }
    };

    const handleSearch = () => {
        // можно добавить фильтрацию на клиенте или сервере
        fetchWorkflows();
    };

    const columns = [
        {
            title: 'Name',
            dataIndex: 'name',
            key: 'name',
        },
        {
            title: 'Description',
            dataIndex: 'description',
            key: 'description',
        },
        {
            title: 'Last Modified',
            dataIndex: 'lastModified',
            key: 'lastModified',
        },
        {
            title: 'Status',
            dataIndex: 'status',
            key: 'status',
        },
        {
            title: 'Actions',
            key: 'actions',
            render: (_: any, record: Workflow) => (
                <Space>
                    <Button type="link" onClick={() => navigate(`/workflows/${record.id}`)}>
                        View
                    </Button>
                </Space>
            ),
        },
    ];

    return (
        <Card title="Workflows" style={{ margin: '20px' }}>
            <Space style={{ marginBottom: 16 }}>
                <Input
                    placeholder="Search workflows"
                    value={searchText}
                    onChange={(e) => setSearchText(e.target.value)}
                    onPressEnter={handleSearch}
                    style={{ width: 200 }}
                />
                <Button icon={<SearchOutlined />} onClick={handleSearch}>
                    Search
                </Button>
                <Button icon={<ReloadOutlined />} onClick={fetchWorkflows}>
                    Refresh
                </Button>
            </Space>
            <Table
                columns={columns}
                dataSource={workflows.filter((wf) =>
                    wf.name.toLowerCase().includes(searchText.toLowerCase())
                )}
                rowKey="id"
                loading={loading}
            />
        </Card>
    );
};

export default Workflows;
