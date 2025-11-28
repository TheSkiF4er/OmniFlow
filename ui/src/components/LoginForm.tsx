import React, { useState } from 'react';
import { Form, Input, Button, Checkbox, message } from 'antd';
import axios from 'axios';

const LoginForm: React.FC = () => {
    const [loading, setLoading] = useState<boolean>(false);

    const onFinish = async (values: any) => {
        setLoading(true);
        try {
            const response = await axios.post('/api/auth/login', values);
            if (response.data.success) {
                message.success('Login successful!');
                // Redirect to dashboard
                window.location.href = '/dashboard';
            } else {
                message.error('Invalid credentials');
            }
        } catch (error) {
            console.error('Login error:', error);
            message.error('Login failed, please try again.');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div style={{ maxWidth: 400, margin: '100px auto' }}>
            <h2>OmniFlow Login</h2>
            <Form
                name="login"
                initialValues={{ remember: true }}
                onFinish={onFinish}
                layout="vertical"
            >
                <Form.Item
                    label="Username"
                    name="username"
                    rules={[{ required: true, message: 'Please input your username!' }]}
                >
                    <Input placeholder="Enter username" />
                </Form.Item>

                <Form.Item
                    label="Password"
                    name="password"
                    rules={[{ required: true, message: 'Please input your password!' }]}
                >
                    <Input.Password placeholder="Enter password" />
                </Form.Item>

                <Form.Item name="remember" valuePropName="checked">
                    <Checkbox>Remember me</Checkbox>
                </Form.Item>

                <Form.Item>
                    <Button type="primary" htmlType="submit" loading={loading} block>
                        Login
                    </Button>
                </Form.Item>
            </Form>
        </div>
    );
};

export default LoginForm;
