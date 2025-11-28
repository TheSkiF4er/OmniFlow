import { configureStore, createSlice, PayloadAction } from '@reduxjs/toolkit';

// --- User Slice ---
interface UserState {
    id: string | null;
    name: string;
    email: string;
    token: string | null;
}

const initialUserState: UserState = {
    id: null,
    name: '',
    email: '',
    token: null,
};

const userSlice = createSlice({
    name: 'user',
    initialState: initialUserState,
    reducers: {
        setUser: (state, action: PayloadAction<UserState>) => {
            return { ...state, ...action.payload };
        },
        clearUser: () => initialUserState,
    },
});

// --- Workflow Slice ---
interface WorkflowState {
    nodes: any[];
    edges: any[];
    currentWorkflowId: string | null;
}

const initialWorkflowState: WorkflowState = {
    nodes: [],
    edges: [],
    currentWorkflowId: null,
};

const workflowSlice = createSlice({
    name: 'workflow',
    initialState: initialWorkflowState,
    reducers: {
        setWorkflowData: (state, action: PayloadAction<{ nodes: any[]; edges: any[] }>) => {
            state.nodes = action.payload.nodes;
            state.edges = action.payload.edges;
        },
        setCurrentWorkflowId: (state, action: PayloadAction<string>) => {
            state.currentWorkflowId = action.payload;
        },
        clearWorkflow: () => initialWorkflowState,
    },
});

// --- Settings Slice ---
interface SettingsState {
    apiKey: string;
    notifications: boolean;
    workflowTimeout: number;
}

const initialSettingsState: SettingsState = {
    apiKey: '',
    notifications: true,
    workflowTimeout: 60,
};

const settingsSlice = createSlice({
    name: 'settings',
    initialState: initialSettingsState,
    reducers: {
        updateSettings: (state, action: PayloadAction<Partial<SettingsState>>) => {
            return { ...state, ...action.payload };
        },
        resetSettings: () => initialSettingsState,
    },
});

// --- Configure Store ---
const store = configureStore({
    reducer: {
        user: userSlice.reducer,
        workflow: workflowSlice.reducer,
        settings: settingsSlice.reducer,
    },
});

// --- Export Types & Actions ---
export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;

export const { setUser, clearUser } = userSlice.actions;
export const { setWorkflowData, setCurrentWorkflowId, clearWorkflow } = workflowSlice.actions;
export const { updateSettings, resetSettings } = settingsSlice.actions;

export default store;
      
