import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { Provider, useSelector } from 'react-redux';
import store, { RootState } from './store';

import Home from './pages/Home';
import Workflows from './pages/Workflows';
import Settings from './pages/Settings';
import Login from './pages/LoginForm';
import { Layout } from 'antd';

const { Header, Content } = Layout;

// --- Protected Route Component ---
const ProtectedRoute: React.FC<{ children: JSX.Element }> = ({ children }) => {
    const token = useSelector((state: RootState) => state.user.token);
    return token ? children : <Navigate to="/login" />;
};

const AppContent: React.FC = () => (
    <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ color: '#fff', fontSize: '20px' }}>OmniFlow</Header>
        <Content style={{ padding: '20px' }}>
            <Routes>
                <Route path="/login" element={<Login />} />
                <Route
                    path="/home"
                    element={
                        <ProtectedRoute>
                            <Home />
                        </ProtectedRoute>
                    }
                />
                <Route
                    path="/workflows"
                    element={
                        <ProtectedRoute>
                            <Workflows />
                        </ProtectedRoute>
                    }
                />
                <Route
                    path="/settings"
                    element={
                        <ProtectedRoute>
                            <Settings />
                        </ProtectedRoute>
                    }
                />
                <Route path="*" element={<Navigate to="/home" />} />
            </Routes>
        </Content>
    </Layout>
);

const App: React.FC = () => (
    <Provider store={store}>
        <Router>
            <AppContent />
        </Router>
    </Provider>
);

export default App;
