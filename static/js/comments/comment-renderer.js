/**
 * Comment Renderer Module
 * Handles rendering of comments and replies
 */
const CommentRenderer = {
  /**
   * Render a single comment
   */
  renderComment(commentData, options = {}) {
    const {
      isReply = false,
      nestingLevel = 0,
      showActions = true,
      currentUser = null
    } = options;

    const isOwner = currentUser && commentData.author.id === currentUser.id;
    const avatarUrl = commentData.author.profile_pic || '/static/images/default_user.jpg';
    const authorName = commentData.author.full_name || commentData.author.username;
    const timestamp = this.formatTimestamp(commentData.created_at);
    const replyCount = commentData.reply_count || 0;
    const profileUrl = `/users/user/${encodeURIComponent(commentData.author.username)}/`;

    const commentHtml = `
      <div class="comment-item ${isReply ? 'reply-item' : ''} ${nestingLevel > 1 ? `reply-item--level-${nestingLevel}` : ''}" 
           id="comment-${commentData.id}"
           data-comment-id="${commentData.id}"
           data-parent-id="${commentData.parent_comment_id || ''}"
           data-reply-count="${replyCount}"
           data-author-username="${commentData.author.username}"
           data-author-id="${commentData.author.id}">
        
        <a href="${profileUrl}"
           hx-get="${profileUrl}"
           hx-target="#page-content-target"
           hx-swap="innerHTML"
           hx-push-url="true"
           class="comment-avatar-link">
          <img src="${avatarUrl}" 
               alt="${authorName}" 
               class="comment-avatar"
               loading="lazy">
        </a>
        
        <div class="comment-content">
          <div class="comment-header">
            <a href="${profileUrl}"
               hx-get="${profileUrl}"
               hx-target="#page-content-target"
               hx-swap="innerHTML"
               hx-push-url="true"
               class="comment-author-link"
               data-username="${commentData.author.username}">
              ${authorName}
            </a>
            <span class="comment-timestamp">${timestamp}</span>
          </div>
          
          <div class="comment-body" id="comment-body-${commentData.id}">
            ${this.escapeHtml(commentData.content)}
          </div>
          ${this.renderAttachment(commentData)}
          
          ${showActions ? this.renderActions(commentData, isOwner, replyCount) : ''}
          
          ${replyCount > 0 ? this.renderReplyToggle(commentData.id, replyCount) : ''}
        </div>
      </div>
    `;

    return commentHtml;
  },

  /**
   * Render comment attachment (TikTok sticker / GIF)
   */
  renderAttachment(commentData) {
    const type = commentData.attachment_type;
    const url = commentData.attachment_url || (commentData.attachment_image ? commentData.attachment_image : '');
    if (!type || type === 'none' || !url) return '';

    if (type === 'sticker' || type === 'image') {
      return `
        <div class="comment-rendered-attachment">
          <img src="${this.escapeHtml(url)}" class="comment-sticker-image" alt="Sticker" loading="lazy" onclick="if(window.openFullscreenImage) window.openFullscreenImage(this.src);">
        </div>
      `;
    } else if (type === 'gif') {
      return `
        <div class="comment-rendered-attachment">
          <img src="${this.escapeHtml(url)}" class="comment-gif-image" alt="GIF" loading="lazy">
        </div>
      `;
    }
    return '';
  },

  /**
   * Render comment actions
   */
  renderActions(commentData, isOwner, replyCount) {
    const likeClass = commentData.is_liked ? 'liked' : '';

    return `
      <div class="comment-actions">
        <button class="comment-action ${likeClass}" 
                data-action="like"
                data-comment-id="${commentData.id}"
                aria-label="Like comment">
          <i aria-hidden="true" data-lucide="heart" class="vasto-icon${commentData.is_liked ? ' vasto-icon--filled' : ''}"></i>
          <span>${commentData.like_count || 0}</span>
        </button>
        
        <button class="comment-action" 
                data-action="reply"
                data-comment-id="${commentData.id}"
                aria-label="Reply to comment">
          <i aria-hidden="true" data-lucide="message-circle" class="vasto-icon"></i>
          Reply
        </button>
        
        <div class="comment-menu-wrapper position-relative d-inline-flex">
          <button class="comment-menu-btn" 
                  data-action="menu"
                  data-comment-id="${commentData.id}"
                  aria-label="More options"
                  aria-expanded="false">
            <i aria-hidden="true" data-lucide="ellipsis" class="vasto-icon"></i>
          </button>
        </div>
      </div>
    `;
  },

  /**
   * Render reply toggle button
   */
  renderReplyToggle(commentId, replyCount) {
    return `
      <button class="reply-toggle" 
              data-action="toggle-replies"
              data-comment-id="${commentId}"
              aria-expanded="false"
              aria-controls="replies-${commentId}">
        <i aria-hidden="true" data-lucide="chevron-down" class="vasto-icon"></i>
        <span>View ${replyCount} ${replyCount === 1 ? 'reply' : 'replies'}</span>
      </button>
    `;
  },

  /**
   * Render inline composer
   */
  renderComposer(commentId, parentAuthorName) {
    return `
      <div class="comment-composer" id="composer-${commentId}">
        <div class="composer-header">
          Replying to <span>${this.escapeHtml(parentAuthorName)}</span>
          <button class="composer-close" data-action="close-composer" aria-label="Cancel reply">
            <i aria-hidden="true" data-lucide="x" class="vasto-icon"></i>
          </button>
        </div>
        <div class="comment-attachment-preview-container"></div>
        <textarea class="composer-input" 
                  id="composer-input-${commentId}"
                  placeholder="Write your reply..."
                  rows="2"
                  aria-label="Reply content"></textarea>
        <div class="composer-actions">
          <div class="composer-tools">
            <button type="button" class="composer-tool-btn composer-media-trigger" data-media-picker data-input="#composer-input-${commentId}" aria-label="Emoji, GIFs and Stickers" title="Emoji, GIFs & Stickers">
              <i aria-hidden="true" data-lucide="smile" class="vasto-icon"></i>
            </button>
            <label class="composer-tool-btn mb-0" style="cursor: pointer;" title="Attach photo as sticker">
              <i aria-hidden="true" data-lucide="camera" class="vasto-icon"></i>
              <input type="file" class="comment-photo-input d-none" accept="image/*">
            </label>
          </div>
          <button class="composer-send" 
                  data-action="send-reply"
                  data-comment-id="${commentId}"
                  disabled>
            Send
          </button>
        </div>
      </div>
    `;
  },

  /**
   * Render edit mode
   */
  renderEditMode(commentId, currentContent) {
    return `
      <div class="comment-edit-mode" id="edit-mode-${commentId}">
        <textarea class="edit-textarea" 
                  id="edit-input-${commentId}"
                  rows="3"
                  aria-label="Edit comment">${this.escapeHtml(currentContent)}</textarea>
        <div class="edit-actions">
          <button class="edit-cancel" 
                  data-action="cancel-edit"
                  data-comment-id="${commentId}">
            Cancel
          </button>
          <button class="edit-save" 
                  data-action="save-edit"
                  data-comment-id="${commentId}">
            Save
          </button>
        </div>
      </div>
    `;
  },

  /**
   * Render overflow menu
   */
  renderMenu(commentId, isOwner, isModerator) {
    let menuItems = '';

    if (isOwner) {
      menuItems += `
        <button class="comment-menu-item" data-action="edit" data-comment-id="${commentId}">
          <i aria-hidden="true" data-lucide="pencil" class="vasto-icon"></i> Edit
        </button>
        <button class="comment-menu-item danger" data-action="delete" data-comment-id="${commentId}">
          <i aria-hidden="true" data-lucide="trash-2" class="vasto-icon"></i> Delete
        </button>
        <div class="comment-menu-divider"></div>
      `;
    } else if (isModerator) {
      menuItems += `
        <button class="comment-menu-item danger" data-action="delete" data-comment-id="${commentId}">
          <i aria-hidden="true" data-lucide="trash-2" class="vasto-icon"></i> Delete
        </button>
        <div class="comment-menu-divider"></div>
      `;
    }

    menuItems += `
      <button class="comment-menu-item" data-action="copy-text" data-comment-id="${commentId}">
        <i aria-hidden="true" data-lucide="copy" class="vasto-icon"></i> Copy text
      </button>
      <button class="comment-menu-item" data-action="copy-link" data-comment-id="${commentId}">
        <i aria-hidden="true" data-lucide="link" class="vasto-icon"></i> Copy link
      </button>
      <button class="comment-menu-item" data-action="view-profile" data-comment-id="${commentId}">
        <i aria-hidden="true" data-lucide="user" class="vasto-icon"></i> View profile
      </button>
    `;

    if (isModerator) {
      menuItems += `
        <div class="comment-menu-divider"></div>
        <button class="comment-menu-item" data-action="pin" data-comment-id="${commentId}">
          <i aria-hidden="true" data-lucide="pin" class="vasto-icon"></i> Pin comment
        </button>
        <button class="comment-menu-item" data-action="hide" data-comment-id="${commentId}">
          <i aria-hidden="true" data-lucide="eye-off" class="vasto-icon"></i> Hide comment
        </button>
      `;
    }

    return `
      <div class="comment-menu-dropdown" id="menu-${commentId}" role="menu">
        ${menuItems}
      </div>
    `;
  },

  /**
   * Render empty state
   */
  renderEmptyState() {
    return `
      <div class="comments-empty">
        <i aria-hidden="true" data-lucide="message-circle" class="vasto-icon"></i>
        <p>No comments yet. Be the first to comment.</p>
      </div>
    `;
  },

  /**
   * Render loading state
   */
  renderLoadingState() {
    return `
      <div class="comments-loading">
        <i aria-hidden="true" data-lucide="refresh-cw" class="vasto-icon"></i>
        <p>Loading comments...</p>
      </div>
    `;
  },

  /**
   * Format timestamp
   */
  formatTimestamp(timestamp) {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now - date;
    
    const seconds = Math.floor(diff / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);
    
    if (seconds < 60) {
      return 'just now';
    } else if (minutes < 60) {
      return `${minutes}m ago`;
    } else if (hours < 24) {
      return `${hours}h ago`;
    } else if (days < 7) {
      return `${days}d ago`;
    } else {
      return date.toLocaleDateString();
    }
  },

  /**
   * Escape HTML to prevent XSS
   */
  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
};

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
  module.exports = CommentRenderer;
}
