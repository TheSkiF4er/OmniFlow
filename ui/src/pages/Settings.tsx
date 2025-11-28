import React, { useState, useEffect } from 'react';
import { Form, Input, Button, Switch, message, Card } from 'antd';
import axios from 'axios';

const Settings: React.FC = () => {
    const [form] = Form.useForm();
    const [loading, setLoading] = useState<boolean>(false);
    const [settings, setSettings] = useState<any>({});

    useEffect(() => {
        fetchSettings();
    }, []);

    const fetchSettings = async () => {
        try {
            const response = await axios.get('/api/settings');
            setSettings(response.data);
            form.setFieldsValue(response.data);
        } catch (error) {
            console.error('Error fetching settings:', error);
            message.error('Failed to load settings');
        }
    };

    const onFinish = async (values: any) => {
        setLoading(true);
        try {
            await axios.post('/api/settings', values);
            message.success('Settings saved successfully!');
        } catch (error) {
            console.error('Error saving settings:', error);
            message.error('Failed to save settings');
        } finally {
            setLoading(false);
        }
    };

    return (
        <Card title="Settings" style={{ maxWidth: 600, margin: '20px auto' }}>
            <Form
                form={form}
                layout="vertical"
                initialValues={settings}
                onFinish={onFinish}
            >
                <Form.Item label="API Key" name="apiKey">
                    <Input placeholder="Enter your API key" />
                </Form.Item>

                <Form.Item label="Enable Notifications" name="notifications" valuePropName="checked">
                    <Switch />
                </Form.Item>

                <Form.Item label="Default Workflow Timeout (seconds)" name="workflowTimeout">
                    <Input type="number" placeholder="60" />
                </Form.Item>

                <Form.Item>
                    <Button type="primary" htmlType="submit" loading={loading}>
                        Save Settings
                    </Button>
                </Form.Item>
            </Form>
        </Card>
    );
};

export default Settings;
