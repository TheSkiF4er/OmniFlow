/**
 * Utility functions for OmniFlow Frontend
 */

import { message } from 'antd';

/**
 * Format a timestamp to readable string
 * @param timestamp - ISO string or number
 */
export const formatDate = (timestamp: string | number): string => {
    const date = new Date(timestamp);
    return date.toLocaleString();
};

/**
 * Generate a UUID v4
 */
export const generateUUID = (): string => {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
        const r = (Math.random() * 16) | 0;
        const v = c === 'x' ? r : (r & 0x3) | 0x8;
        return v.toString(16);
    });
};

/**
 * Simple API request wrapper using fetch
 */
export const apiRequest = async (url: string, options: RequestInit = {}) => {
    try {
        const response = await fetch(url, {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers,
            },
            ...options,
        });
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.message || 'API Error');
        }
        return response.json();
    } catch (error: any) {
        console.error('API request failed:', error);
        message.error(error.message || 'Request failed');
        throw error;
    }
};

/**
 * Capitalize first letter of a string
 */
export const capitalize = (str: string): string => {
    if (!str) return '';
    return str.charAt(0).toUpperCase() + str.slice(1);
};

/**
 * Delay / sleep function
 * @param ms milliseconds
 */
export const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
