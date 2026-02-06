import { fetchSessions, fetchSessionMessages, deleteSession, syncSessions, searchSessions } from './api.js';
import { SessionListView } from './sessionList.js';
import { ConversationView } from './conversationView.js';
import { MessageDetailsView } from './messageDetails.js';
import { PanelResizer } from './resizer.js';

class App {
    constructor() {
        this.sessionListView = new SessionListView('sessionList');
        this.conversationView = new ConversationView('conversationHistory');
        this.messageDetailsView = new MessageDetailsView('messageDetails');
        this.panelResizer = new PanelResizer();

        this.currentSessionId = null;
        this.allSessions = [];
        this.activeFilters = new Set(); // Track active filter types

        this.init();
    }

    init() {
        // Set up event listeners
        this.sessionListView.onSessionSelect = (sessionId) => this.loadSession(sessionId);
        this.sessionListView.onSessionDelete = (sessionId) => this.confirmDeleteSession(sessionId);
        this.conversationView.onMessageSelect = (message) => this.showMessageDetails(message);
        this.conversationView.onFilterChange = () => this.renderFilterTags();

        // Reload button
        const reloadBtn = document.getElementById('reloadBtn');
        reloadBtn.addEventListener('click', () => this.reloadSessions());

        // Session search functionality
        const sessionSearchInput = document.getElementById('sessionSearch');

        // Search on Enter key
        sessionSearchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.performSessionSearch(e.target.value);
            }
        });

        // Clear session search button
        const clearSessionSearchBtn = document.getElementById('clearSessionSearchBtn');
        clearSessionSearchBtn.addEventListener('click', () => {
            sessionSearchInput.value = '';
            this.loadSessions(); // Load all sessions
            sessionSearchInput.focus();
        });

        // Message search functionality
        const searchInput = document.getElementById('uuidSearch');

        // Clear highlights when search input changes
        searchInput.addEventListener('input', () => {
            this.conversationView.clearHighlights();
        });

        // Search on Enter key
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.performMessageSearch(e.target.value);
            }
        });

        // Clear search button
        const clearUuidBtn = document.getElementById('clearUuidBtn');
        clearUuidBtn.addEventListener('click', () => {
            searchInput.value = '';
            this.conversationView.clearHighlights();
            searchInput.focus();
        });

        // Filter tags - use event delegation (set up once)
        const filterTags = document.getElementById('filterTags');
        filterTags.addEventListener('click', (e) => {
            const tag = e.target.closest('.filter-tag');
            if (!tag) return;

            const type = tag.dataset.type;
            if (this.activeFilters.has(type)) {
                this.activeFilters.delete(type);
                tag.classList.remove('active');
            } else {
                this.activeFilters.add(type);
                tag.classList.add('active');
            }
            this.applyFilters();
        });

        // Initial load
        this.loadSessions();
    }

    async performSessionSearch(searchTerm) {
        try {
            const sessionList = document.getElementById('sessionList');
            sessionList.innerHTML = '<div class="loading">Searching sessions...</div>';

            const sessions = await searchSessions(searchTerm);
            this.allSessions = sessions;

            this.sessionListView.render(sessions);
        } catch (error) {
            console.error('Failed to search sessions:', error);
            const sessionList = document.getElementById('sessionList');
            sessionList.innerHTML = '<div class="empty-state"><p>Failed to search sessions. Please check the server.</p></div>';
        }
    }

    performMessageSearch(searchTerm) {
        const matchedIndices = this.conversationView.search(searchTerm);
        if (matchedIndices.length > 0) {
            this.conversationView.highlightMessages(matchedIndices);
        } else {
            // Clear highlights if no matches found
            this.conversationView.clearHighlights();
        }
    }

    renderFilterTags() {
        const filterTags = document.getElementById('filterTags');
        const messageTypes = this.conversationView.getAvailableMessageTypes();

        filterTags.innerHTML = messageTypes
            .map(type => {
                const isActive = this.activeFilters.has(type);
                // Use getMessageTypeName to get consistent label
                const label = this.getFilterLabel(type);
                return `<div class="filter-tag ${type} ${isActive ? 'active' : ''}" data-type="${type}">
                    ${label}
                </div>`;
            })
            .join('');

        // Note: Click handler is set up once in init() using event delegation
    }

    getFilterLabel(typeClass) {
        const typeLabels = {
            'user': 'User',
            'assistant': 'Assistant',
            'tool-use': 'Tool Use',
            'tool-result': 'Tool Result',
            'system': 'System',
            'file-history-snapshot': 'File Snapshot',
            'queue-operation': 'Queue Operation',
            'summary': 'Summary'
        };

        // Return mapped label or capitalize the type
        return typeLabels[typeClass] || (typeClass
            ? typeClass.split('-').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ')
            : 'Unknown');
    }

    applyFilters() {
        this.conversationView.filterByTypes(this.activeFilters);
        // Clear search highlights when filters change
        this.conversationView.clearHighlights();
    }

    clearFilters() {
        // Clear filter state
        this.activeFilters.clear();

        // Clear filter tags UI
        const filterTags = document.getElementById('filterTags');
        filterTags.innerHTML = '';
    }

    async reloadSessions() {
        try {
            const sessionList = document.getElementById('sessionList');
            sessionList.innerHTML = '<div class="loading">Syncing sessions...</div>';

            // Call sync API
            const syncStats = await syncSessions();
            console.log('Sync stats:', syncStats);

            // Show sync result briefly
            let syncMessage = '';
            if (syncStats.added > 0 || syncStats.updated > 0) {
                const parts = [];
                if (syncStats.added > 0) parts.push(`${syncStats.added} added`);
                if (syncStats.updated > 0) parts.push(`${syncStats.updated} updated`);
                syncMessage = ` (${parts.join(', ')})`;
            }

            sessionList.innerHTML = `<div class="loading">Synced${syncMessage}. Loading...</div>`;

            // Reload sessions list
            await this.loadSessions();
        } catch (error) {
            console.error('Failed to reload sessions:', error);
            const sessionList = document.getElementById('sessionList');
            sessionList.innerHTML = '<div class="empty-state"><p>Failed to sync sessions. Please check the server.</p></div>';
        }
    }

    async loadSessions() {
        try {
            const sessionList = document.getElementById('sessionList');
            sessionList.innerHTML = '<div class="loading">Loading sessions</div>';

            const sessions = await fetchSessions();
            this.allSessions = sessions;

            sessions.sort((a, b) => b.lastModified - a.lastModified);

            this.sessionListView.render(sessions);
        } catch (error) {
            console.error('Failed to load sessions:', error);
            const sessionList = document.getElementById('sessionList');
            sessionList.innerHTML = '<div class="empty-state"><p>Failed to load sessions. Please check the server.</p></div>';
        }
    }

    async loadSession(sessionId) {
        try {
            this.currentSessionId = sessionId;

            // Show loading
            const conversationHistory = document.getElementById('conversationHistory');
            conversationHistory.innerHTML = '<div class="loading">Loading messages</div>';

            // Fetch messages
            const messages = await fetchSessionMessages(sessionId);

            // Render conversation
            this.conversationView.render(messages);

            // Set default filters (User, Assistant)
            this.activeFilters.clear();
            const defaultFilters = ['user', 'assistant'];
            defaultFilters.forEach(filter => this.activeFilters.add(filter));

            // Render filter tags
            this.renderFilterTags();

            // Apply default filters
            this.applyFilters();

            // Clear message details
            this.messageDetailsView.clear();

            // Clear search highlights when switching sessions
            this.conversationView.clearHighlights();

            // Clear search input
            const searchInput = document.getElementById('uuidSearch');
            if (searchInput) {
                searchInput.value = '';
            }
        } catch (error) {
            console.error('Failed to load session:', error);
            const conversationHistory = document.getElementById('conversationHistory');
            conversationHistory.innerHTML = '<div class="empty-state"><p>Failed to load session messages.</p></div>';
        }
    }

    showMessageDetails(message) {
        this.messageDetailsView.render(message);
    }

    async confirmDeleteSession(sessionPath) {
        const confirmed = confirm(`Are you sure you want to delete session "${sessionPath}"?\n\nThis will delete:\n- Session file\n- Related agent files\n- File history folder\n- Database record\n\nThis action cannot be undone.`);

        if (confirmed) {
            await this.deleteSession(sessionPath);
        }
    }

    async deleteSession(sessionPath) {
        try {
            // Save current selection state
            const previousSessionId = this.currentSessionId;

            const result = await deleteSession(sessionPath);

            if (result.success) {
                // All files deleted successfully
                const deletedCount = result.deleted ? result.deleted.length : 0;
                alert(`Successfully deleted ${deletedCount} file(s).`);

                // Reload sessions list from database (no sync)
                await this.loadSessionsFromDB(previousSessionId, sessionPath);
            } else {
                // Some files deleted but with errors
                const deletedCount = result.deleted ? result.deleted.length : 0;
                const errorCount = result.errors ? result.errors.length : 0;
                let message = `Partial deletion: ${deletedCount} file(s) deleted, ${errorCount} error(s).\n\nErrors:\n${result.errors.join('\n')}`;
                alert(message);

                // Still reload sessions list to reflect changes
                await this.loadSessionsFromDB(previousSessionId, sessionPath);
            }
        } catch (error) {
            console.error('Failed to delete session:', error);
            alert(`Failed to delete session: ${error.message}`);
        }
    }

    async loadSessionsFromDB(previousSessionId, deletedSessionPath) {
        try {
            const sessionList = document.getElementById('sessionList');
            sessionList.innerHTML = '<div class="loading">Loading sessions...</div>';

            // Fetch sessions from database without syncing
            const sessions = await fetchSessions();
            this.allSessions = sessions;

            sessions.sort((a, b) => b.lastModified - a.lastModified);

            this.sessionListView.render(sessions);

            if (previousSessionId && previousSessionId !== deletedSessionPath) {
                const stillExists = sessions.some(s => s.id === previousSessionId);
                if (stillExists) {
                    this.sessionListView.selectSessionUI(previousSessionId);
                } else {
                    this.currentSessionId = null;
                    this.conversationView.clear();
                    this.messageDetailsView.clear();
                    this.clearFilters();
                }
            } else if (previousSessionId === deletedSessionPath) {
                // The deleted session was selected, clear right panels
                this.currentSessionId = null;
                this.conversationView.clear();
                this.messageDetailsView.clear();
                this.clearFilters();
            }
        } catch (error) {
            console.error('Failed to load sessions:', error);
            const sessionList = document.getElementById('sessionList');
            sessionList.innerHTML = '<div class="empty-state"><p>Failed to load sessions. Please check the server.</p></div>';
        }
    }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new App();
});
