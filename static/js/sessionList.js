import { formatTimestampNumeric, escapeHtml } from './utils.js';

export class SessionListView {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.sessions = [];
        this.onSessionSelect = null;
        this.onSessionDelete = null;
    }

    render(sessions) {
        this.sessions = sessions;

        if (!sessions || sessions.length === 0) {
            this.container.innerHTML = '<div class="empty-state"><p>No sessions found</p></div>';
            return;
        }

        this.container.innerHTML = sessions.map(session => this.renderSessionItem(session)).join('');
        this.attachEventListeners();
    }

    renderSessionItem(session) {
        const uuid = session.id;
        const formattedProject = session.project.replace(/--/g, ':/').replace(/-/g, '/');

        return `
            <div class="session-item" data-session-id="${escapeHtml(session.id)}">
                <div class="session-header">
                    <div class="session-project" title="Project: ${escapeHtml(formattedProject)}">
                        ${escapeHtml(formattedProject)}
                    </div>
                    <div class="message-count">${session.messageCount || 0} messages</div>
                </div>
                <div class="session-uuid" title="${escapeHtml(uuid)}">
                    ${escapeHtml(uuid)}
                </div>
                <div class="session-footer">
                    <div class="session-time">${formatTimestampNumeric(session.lastModified)}</div>
                    <button class="session-delete-btn" data-session-id="${escapeHtml(session.id)}" title="Delete session">×</button>
                </div>
            </div>
        `;
    }

    attachEventListeners() {
        const items = this.container.querySelectorAll('.session-item');
        items.forEach(item => {
            // Click on session item to select
            item.addEventListener('click', (e) => {
                // Don't trigger if clicking delete button
                if (e.target.classList.contains('session-delete-btn')) {
                    return;
                }
                this.selectSession(item.dataset.sessionId);
            });
        });

        // Delete button listeners
        const deleteButtons = this.container.querySelectorAll('.session-delete-btn');
        deleteButtons.forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation(); // Prevent session selection
                const sessionId = btn.dataset.sessionId;
                if (this.onSessionDelete) {
                    this.onSessionDelete(sessionId);
                }
            });
        });
    }

    selectSession(sessionId) {
        // Update UI
        this.selectSessionUI(sessionId);

        // Trigger callback
        if (this.onSessionSelect) {
            this.onSessionSelect(sessionId);
        }
    }

    selectSessionUI(sessionId) {
        // Update UI only, without triggering callback
        const items = this.container.querySelectorAll('.session-item');
        items.forEach(item => {
            if (item.dataset.sessionId === sessionId) {
                item.classList.add('active');
            } else {
                item.classList.remove('active');
            }
        });
    }
}
