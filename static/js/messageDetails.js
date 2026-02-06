import { formatJson, escapeHtml } from './utils.js';

export class MessageDetailsView {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.currentMessage = null;
    }

    render(message) {
        this.currentMessage = message;

        if (!message) {
            this.container.innerHTML = '<div class="empty-state"><p>Click a message to view details</p></div>';
            return;
        }

        const sections = [];

        // Raw JSON Section - always show first
        sections.push(this.renderRawJson(message));

        // Message Content Section
        if (message.message) {
            const contentSection = this.renderMessageContent(message);
            if (contentSection) {
                sections.push(contentSection);
            }
        }

        this.container.innerHTML = sections.join('');
    }

    renderMessageContent(message) {
        const msg = message.message;

        if (!msg || !msg.content) {
            return '';
        }

        let contentHtml = '';

        // Render content blocks
        if (typeof msg.content === 'string') {
            contentHtml = `
                <div class="content-block text">
                    <div class="content-block-header">
                        <div class="content-type">Text</div>
                        <button class="wrap-toggle" onclick="toggleWrap(event)" title="Toggle wrap">⏎</button>
                    </div>
                    <pre class="content-text">${escapeHtml(msg.content)}</pre>
                </div>`;
        } else if (Array.isArray(msg.content)) {
            contentHtml = msg.content.map(block => this.renderContentBlock(block)).join('');
        }

        if (!contentHtml) {
            return '';
        }

        return `
            <div class="detail-section">
                <h3>Message Content</h3>
                ${contentHtml}
            </div>
        `;
    }

    renderContentBlock(block) {
        if (block.type === 'text') {
            return `
                <div class="content-block text">
                    <div class="content-block-header">
                        <div class="content-type">Text</div>
                        <button class="wrap-toggle" onclick="toggleWrap(event)" title="Toggle wrap">⏎</button>
                    </div>
                    <pre class="content-text">${escapeHtml(block.text || '')}</pre>
                </div>
            `;
        }

        if (block.type === 'tool_use') {
            return `
                <div class="content-block tool_use">
                    <div class="content-block-header">
                        <div class="content-type">Tool Use</div>
                        <button class="wrap-toggle" onclick="toggleWrap(event)" title="Toggle wrap">⏎</button>
                    </div>
                    <div class="tool-name">${escapeHtml(block.name || 'Unknown')}</div>
                    <pre class="content-text">${escapeHtml(formatJson(block.input || {}))}</pre>
                </div>
            `;
        }

        if (block.type === 'tool_result') {
            return `
                <div class="content-block tool_result">
                    <div class="content-block-header">
                        <div class="content-type">Tool Result</div>
                        <button class="wrap-toggle" onclick="toggleWrap(event)" title="Toggle wrap">⏎</button>
                    </div>
                    <pre class="content-text">${escapeHtml(block.content || '')}</pre>
                </div>
            `;
        }

        return `
            <div class="content-block">
                <div class="content-block-header">
                    <div class="content-type">${escapeHtml(block.type || 'Unknown')}</div>
                    <button class="wrap-toggle" onclick="toggleWrap(event)" title="Toggle wrap">⏎</button>
                </div>
                <pre class="content-text">${escapeHtml(formatJson(block))}</pre>
            </div>
        `;
    }

    renderRawJson(message) {
        return `
            <div class="detail-section">
                <h3>Raw JSON</h3>
                <div class="json-view">${escapeHtml(formatJson(message))}</div>
            </div>
        `;
    }

    clear() {
        this.container.innerHTML = '<div class="empty-state"><p>Click a message to view details</p></div>';
        this.currentMessage = null;
    }
}
