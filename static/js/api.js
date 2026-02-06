// API endpoint configuration - use empty string for relative paths
const API_BASE_URL = '';

export async function fetchSessions(projectFilter = null) {
    try {
        const url = projectFilter
            ? `${API_BASE_URL}/sessions?project=${encodeURIComponent(projectFilter)}`
            : `${API_BASE_URL}/sessions`;
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error('Error fetching sessions:', error);
        throw error;
    }
}

export async function searchSessions(searchTerm = '') {
    try {
        const url = `${API_BASE_URL}/sessions/search?q=${encodeURIComponent(searchTerm)}`;
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error('Error searching sessions:', error);
        throw error;
    }
}

export async function syncSessions() {
    try {
        const response = await fetch(`${API_BASE_URL}/sessions/sync`, {
            method: 'POST'
        });
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error('Error syncing sessions:', error);
        throw error;
    }
}

export async function fetchSessionMessages(sessionPath) {
    try {
        const response = await fetch(`${API_BASE_URL}/session/${sessionPath}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error('Error fetching session messages:', error);
        throw error;
    }
}

export async function deleteSession(sessionPath) {
    try {
        const response = await fetch(`${API_BASE_URL}/session/${sessionPath}`, {
            method: 'DELETE'
        });
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error('Error deleting session:', error);
        throw error;
    }
}
