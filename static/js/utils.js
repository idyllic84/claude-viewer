export function formatTime(timestamp) {
    if (!timestamp) return 'N/A';
    const date = typeof timestamp === 'string' ? new Date(timestamp) : new Date(timestamp * 1000);
    return date.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
    });
}

export function formatTimestampNumeric(timestamp) {
    if (!timestamp) return 'N/A';
    const date = typeof timestamp === 'string' ? new Date(timestamp) : new Date(timestamp * 1000);
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const hour = String(date.getHours()).padStart(2, '0');
    const minute = String(date.getMinutes()).padStart(2, '0');
    const second = String(date.getSeconds()).padStart(2, '0');
    return `${year}-${month}-${day} ${hour}:${minute}:${second}`;
}

function truncateText(text, maxLength = 150) {
    if (!text || text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
}

export function extractMessagePreview(message) {
    if (!message || !message.content) return '';

    const content = message.content;

    // If content is a string
    if (typeof content === 'string') {
        return truncateText(content);
    }

    // If content is an array (Claude API format)
    if (Array.isArray(content)) {
        // Check if this is a tool_use message (has any tool_use block)
        const hasToolUse = content.some(block => block.type === 'tool_use');

        if (hasToolUse) {
            // Find the first tool_use block
            const toolUseBlock = content.find(block => block.type === 'tool_use');
            if (toolUseBlock) {
                const name = toolUseBlock.name || 'Unknown';
                const input = toolUseBlock.input ? JSON.stringify(toolUseBlock.input) : '';
                return input ? `${name}: ${truncateText(input, 100)}` : name;
            }
        }

        // For assistant messages without tool_use, show first text block
        const firstTextBlock = content.find(block => block.type === 'text');
        if (firstTextBlock && firstTextBlock.text) {
            return truncateText(firstTextBlock.text);
        }

        // Fallback for tool_result
        const toolResultBlock = content.find(block => block.type === 'tool_result');
        if (toolResultBlock) {
            const resultContent = typeof toolResultBlock.content === 'string'
                ? toolResultBlock.content
                : JSON.stringify(toolResultBlock.content);
            return truncateText(resultContent, 100);
        }
    }

    return '[No preview available]';
}

// Detect the primary message content type
export function detectMessageContentType(message) {
    if (!message || !message.content) return null;

    const content = message.content;

    if (Array.isArray(content)) {
        // Check if message contains tool_use
        const hasToolUse = content.some(block => block.type === 'tool_use');
        if (hasToolUse) return 'tool_use';

        // Check if message contains tool_result
        const hasToolResult = content.some(block => block.type === 'tool_result');
        if (hasToolResult) return 'tool_result';

        // Check if it's text
        const hasText = content.some(block => block.type === 'text');
        if (hasText) return 'text';
    }

    return null;
}

export function getMessageTypeClass(message) {
    const type = message.type;

    if (type === 'user' || type === 'assistant') {
        const contentType = detectMessageContentType(message.message);
        if (contentType === 'tool_use') return 'tool-use';
        if (contentType === 'tool_result') return 'tool-result';
    }

    const typeMap = {
        'user': 'user',
        'assistant': 'assistant',
        'system': 'system',
        'file-history-snapshot': 'file-history-snapshot',
        'queue-operation': 'queue-operation'
    };

    return typeMap[type] || (type ? type.toLowerCase().replace(/[^a-z0-9-]/g, '-') : 'unknown');
}

export function getMessageTypeName(message) {
    const type = message.type;
    const contentType = (type === 'user' || type === 'assistant')
        ? detectMessageContentType(message.message)
        : null;

    if (contentType === 'tool_use') return 'Tool Use';
    if (contentType === 'tool_result') return 'Tool Result';

    const typeNames = {
        'user': 'User',
        'assistant': 'Assistant',
        'system': 'System',
        'file-history-snapshot': 'File Snapshot',
        'queue-operation': 'Queue Operation',
        'summary': 'Summary'
    };

    return typeNames[type] || (type
        ? type.split('-').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ')
        : 'Unknown');
}

export function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

export function formatJson(obj) {
    return JSON.stringify(obj, null, 2);
}

// Extract full text content from a message object for searching
export function extractFullMessageText(message) {
    if (!message) return '';

    let textParts = [];

    // Extract UUID
    if (message.uuid) {
        textParts.push(message.uuid);
    }

    // Extract summary (for summary type messages)
    if (message.summary) {
        textParts.push(message.summary);
    }

    // Extract from message.message.content (user/assistant messages)
    if (message.message && message.message.content) {
        const content = message.message.content;

        if (typeof content === 'string') {
            textParts.push(content);
        } else if (Array.isArray(content)) {
            // Extract text from all content blocks
            content.forEach(block => {
                if (block.type === 'text' && block.text) {
                    textParts.push(block.text);
                } else if (block.type === 'tool_use') {
                    if (block.name) textParts.push(block.name);
                    if (block.input) textParts.push(JSON.stringify(block.input));
                } else if (block.type === 'tool_result') {
                    if (typeof block.content === 'string') {
                        textParts.push(block.content);
                    } else if (block.content) {
                        textParts.push(JSON.stringify(block.content));
                    }
                }
            });
        }
    }

    // Extract from message.content (other message types)
    if (message.content) {
        if (typeof message.content === 'string') {
            textParts.push(message.content);
        } else {
            textParts.push(JSON.stringify(message.content));
        }
    }

    // Extract from file-history-snapshot
    if (message.snapshot && message.snapshot.trackedFileBackups) {
        const files = Object.keys(message.snapshot.trackedFileBackups);
        textParts.push(files.join(' '));
    }

    // Join all text parts
    return textParts.join(' ');
}
