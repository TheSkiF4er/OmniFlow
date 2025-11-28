import { createSlice, PayloadAction } from '@reduxjs/toolkit';
import { Node, Edge } from 'react-flow-renderer';

interface WorkflowState {
    nodes: Node[];
    edges: Edge[];
    currentWorkflowId: string | null;
}

const initialState: WorkflowState = {
    nodes: [],
    edges: [],
    currentWorkflowId: null,
};

const workflowSlice = createSlice({
    name: 'workflow',
    initialState,
    reducers: {
        setWorkflowData: (state, action: PayloadAction<{ nodes: Node[]; edges: Edge[] }>) => {
            state.nodes = action.payload.nodes;
            state.edges = action.payload.edges;
        },
        setCurrentWorkflowId: (state, action: PayloadAction<string>) => {
            state.currentWorkflowId = action.payload;
        },
        addNode: (state, action: PayloadAction<Node>) => {
            state.nodes.push(action.payload);
        },
        addEdge: (state, action: PayloadAction<Edge>) => {
            state.edges.push(action.payload);
        },
        clearWorkflow: () => initialState,
    },
});

export const { setWorkflowData, setCurrentWorkflowId, addNode, addEdge, clearWorkflow } = workflowSlice.actions;

export default workflowSlice.reducer;
