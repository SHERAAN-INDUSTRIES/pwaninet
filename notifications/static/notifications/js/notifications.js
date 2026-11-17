/**
 * Notification Center JavaScript Module
 * Handles filter changes, search, UI interactions, and HTMX event listeners
 */

/**
 * Handle search input with debouncing
 * Searches notifications and updates URL
 */
let searchTimeout;
export function handleSearch(event) {
  const searchInput = document.getElementById('search-input');
  const query = searchInput.value.trim();
  
  // Clear previous timeout
  clearTimeout(searchTimeout);
  
  // Debounce search (wait 500ms after user stops typing)
  searchTimeout = setTimeout(() => {
    const url = new URL(window.location);
    
    // Update or remove search parameter
    if (query) {
      url.searchParams.set('search', query);
    } else {
      url.searchParams.delete('search');
    }
    
    window.location.href = url.toString();
  }, 500);
}

/**
 * Handle filter dropdown changes
 * Updates URL with selected filter parameters and reloads page
 */
export function handleFilterChange() {
  const select = document.getElementById('filter-select');
  const value = select.value;
  
  if (value === '') {
    // Go to base URL (all notifications)
    window.location.href = window.location.pathname;
  } else {
    // Build URL with parameters
    const url = new URL(window.location);
    
    // Clear existing parameters
    url.searchParams.delete('type');
    url.searchParams.delete('read');
    url.searchParams.delete('time');
    url.searchParams.delete('grouped');
    url.searchParams.delete('sender_grouped');
    url.searchParams.delete('hybrid_grouped');
    
    // Add new parameter
    const [key, val] = value.split('=');
    url.searchParams.set(key, val);
    
    window.location.href = url.toString();
  }
}

/**
 * Apply quick filter from filter chips
 */
export function applyQuickFilter(filterValue) {
  const url = new URL(window.location);
  
  // Clear existing filter parameters
  url.searchParams.delete('type');
  url.searchParams.delete('read');
  url.searchParams.delete('time');
  url.searchParams.delete('grouped');
  url.searchParams.delete('sender_grouped');
  url.searchParams.delete('hybrid_grouped');
  
  // Add new filter
  const [key, val] = filterValue.split('=');
  url.searchParams.set(key, val);
  
  window.location.href = url.toString();
}

/**
 * Clear search query
 */
export function clearSearch() {
  const url = new URL(window.location);
  url.searchParams.delete('search');
  window.location.href = url.toString();
}

/**
 * Toggle visibility of older notifications section
 * Used for time-grouped notifications to show/hide older items
 */
export function toggleOlderNotifications() {
  const olderSection = document.getElementById('older-section');
  const viewMoreBtn = document.querySelector('.btn-view-more');
  
  if (olderSection.style.display === 'none') {
    olderSection.style.display = 'block';
    viewMoreBtn.innerHTML = '<i aria-hidden="true" data-lucide="chevron-up" class="vasto-icon"></i> View less';
    viewMoreBtn.classList.add('expanded');
  } else {
    olderSection.style.display = 'none';
    viewMoreBtn.innerHTML = '<i aria-hidden="true" data-lucide="chevron-down" class="vasto-icon"></i> View more';
    viewMoreBtn.classList.remove('expanded');
  }
  if (window.VastoIcons) window.VastoIcons.render(viewMoreBtn);
}

/**
 * Initialize HTMX event listeners
 * Handles unread count updates after HTMX swaps
 */
export function initHtmxListeners() {
  const updateCountsHandler = function() {
    fetch('/notifications/unread-count/?format=json', {
      headers: { 'Accept': 'application/json' }
    })
      .then(r => r.json())
      .then(data => {
        if (data && typeof data.unread_count !== 'undefined') {
          const badge = document.getElementById('unread-count-badge');
          if (badge) {
            if (data.unread_count > 0) {
              badge.textContent = `${data.unread_count} unread`;
              badge.style.display = 'inline-block';
            } else {
              badge.style.display = 'none';
            }
          }
          const navBadges = document.querySelectorAll('.notification-badge');
          navBadges.forEach(nb => {
            if (data.unread_count > 0) {
              nb.textContent = data.unread_count > 99 ? '99+' : data.unread_count;
              nb.style.display = 'flex';
            } else {
              nb.textContent = '0';
              nb.style.display = 'none';
            }
          });
        }
      })
      .catch(() => {});
      
    // Update group unread badges via API
    fetch('/groups/api/unread-counts/')
      .then(r => r.json())
      .then(counts => {
        document.querySelectorAll('.unread-badge').forEach(badge => {
          const groupId = badge.getAttribute('data-group-id');
          if (groupId && counts[groupId] !== undefined) {
            const count = counts[groupId];
            if (count > 0) {
              badge.textContent = count;
              badge.style.display = 'inline-flex';
              badge.classList.add('pulsing');
              setTimeout(() => badge.classList.remove('pulsing'), 600);
            } else {
              badge.style.display = 'none';
            }
          }
        });
      })
      .catch(() => {});
  };

  document.body.addEventListener('updateUnreadCount', updateCountsHandler);
  
  // Add arrival animation to new notifications
  document.body.addEventListener('htmx:afterSwap', function(evt) {
    if (evt.detail.target.id === 'notification-list') {
      const newItems = evt.detail.target.querySelectorAll('.notif-item');
      newItems.forEach((item, index) => {
        // Stagger animations
        setTimeout(() => {
          item.classList.add('arriving');
          setTimeout(() => item.classList.remove('arriving'), 400);
        }, index * 50);
      });
    }
  });
  
  // Add dismissal animation before deletion
  document.body.addEventListener('htmx:beforeRequest', function(evt) {
    const target = evt.detail.target;
    if (target && target.classList.contains('notif-item')) {
      const isDelete = evt.detail.requestConfig?.headers?.includes('delete') ||
                       evt.detail.requestConfig?.verb === 'delete';
      if (isDelete) {
        target.classList.add('dismissing');
        // Delay the request to allow animation to play
        evt.preventDefault();
        setTimeout(() => {
          htmx.trigger(evt.detail.elt, evt.detail.triggerSpec);
        }, 300);
      }
    }
  });
}

/**
 * Initialize notification center
 * Call this on page load to set up all event listeners
 */
export function initNotificationCenter() {
  // Make functions available globally for inline event handlers
  window.handleFilterChange = handleFilterChange;
  window.toggleOlderNotifications = toggleOlderNotifications;
  window.handleSearch = handleSearch;
  window.applyQuickFilter = applyQuickFilter;
  window.clearSearch = clearSearch;
  window.toggleSelection = toggleSelection;
  window.selectAllNotifications = selectAllNotifications;
  window.deselectAllNotifications = deselectAllNotifications;
  window.toggleSelectionMode = toggleSelectionMode;
  window.markSelectedAsRead = markSelectedAsRead;
  window.deleteSelected = deleteSelected;
  window.loadMoreNotifications = loadMoreNotifications;
  
  // Initialize HTMX listeners
  initHtmxListeners();
  
  // Initialize lazy loading
  initLazyLoading();
}

/**
 * Initialize lazy loading for notifications
 */
export function initLazyLoading() {
  const loadMoreTrigger = document.getElementById('load-more-trigger');
  
  if (loadMoreTrigger && loadMoreTrigger.dataset.cursor) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting && loadMoreTrigger.dataset.loading !== 'true' && loadMoreTrigger.dataset.cursor) {
          loadMoreNotifications();
        }
      });
    }, {
      rootMargin: '200px',
      threshold: 0.1
    });
    
    observer.observe(loadMoreTrigger);
  } else if (loadMoreTrigger) {
    loadMoreTrigger.style.display = 'none';
  }
}

/**
 * Load more notifications via infinite scroll with cursor-based pagination
 */
export function loadMoreNotifications() {
  const loadMoreTrigger = document.getElementById('load-more-trigger');
  if (!loadMoreTrigger) return;
  const currentCursor = loadMoreTrigger.dataset.cursor || '';
  
  // Do not request more if there is no cursor
  if (!currentCursor) {
    loadMoreTrigger.style.display = 'none';
    return;
  }
  
  // Prevent duplicate requests
  if (loadMoreTrigger.dataset.loading === 'true') {
    return;
  }
  
  loadMoreTrigger.dataset.loading = 'true';
  
  // Show loading indicator
  const loadingIndicator = document.getElementById('loading-indicator');
  if (loadingIndicator) {
    loadingIndicator.style.display = 'block';
  }
  
  // Get current URL parameters
  const url = new URL(window.location);
  url.searchParams.set('cursor', currentCursor);
  url.searchParams.set('partial', 'true');
  url.searchParams.set('limit', '10');
  
  // Fetch next page with cursor
  fetch(url.toString())
    .then(response => response.json())
    .then(data => {
      if (data.html && data.html.trim()) {
        // Append new notifications with time sections
        const notificationList = document.getElementById('notification-list');
        const cardBody = notificationList.querySelector('.card-body');
        
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = data.html;
        
        // Check for duplicate time sections and merge them
        const newTimeSections = tempDiv.querySelectorAll('.time-section');
        const existingTimeSections = cardBody.querySelectorAll('.time-section');
        
        newTimeSections.forEach(newSection => {
          const newHeader = newSection.querySelector('.time-section-header h6');
          if (!newHeader) return;
          
          const newHeaderText = newHeader.textContent.trim();
          let shouldAppend = true;
          
          // Check if this time section already exists
          existingTimeSections.forEach(existingSection => {
            const existingHeader = existingSection.querySelector('.time-section-header h6');
            if (existingHeader && existingHeader.textContent.trim() === newHeaderText) {
              // Append only the notification items, not the section header
              const newItems = newSection.querySelectorAll('.list-group-item');
              newItems.forEach(item => {
                const notifId = item.dataset.notificationId;
                if (notifId && cardBody.querySelector(`[data-notification-id="${notifId}"]`)) {
                  return; // Skip duplicate notification
                }
                item.classList.add('arriving');
                setTimeout(() => item.classList.remove('arriving'), 400);
                existingSection.appendChild(item);
              });
              shouldAppend = false;
            }
          });
          
          // If section doesn't exist, append the section with deduplicated items
          if (shouldAppend) {
            const newItems = newSection.querySelectorAll('.list-group-item');
            let hasNewItems = false;
            newItems.forEach(item => {
              const notifId = item.dataset.notificationId;
              if (notifId && cardBody.querySelector(`[data-notification-id="${notifId}"]`)) {
                item.remove();
              } else {
                hasNewItems = true;
                item.classList.add('arriving');
                setTimeout(() => item.classList.remove('arriving'), 400);
              }
            });
            if (hasNewItems) {
              cardBody.appendChild(newSection);
            }
          }
        });
        
        // Clean up tempDiv
        tempDiv.remove();
        
        // Update cursor for next request
        if (data.next_cursor) {
          loadMoreTrigger.dataset.cursor = data.next_cursor;
        } else {
          // No more items, hide trigger
          loadMoreTrigger.style.display = 'none';
        }
        
        // Check if there are more items
        if (!data.has_more) {
          loadMoreTrigger.style.display = 'none';
        }
      } else {
        // No more notifications
        loadMoreTrigger.style.display = 'none';
      }
    })
    .catch(error => {
      console.error('Error loading more notifications:', error);
    })
    .finally(() => {
      loadMoreTrigger.dataset.loading = 'false';
      if (loadingIndicator) {
        loadingIndicator.style.display = 'none';
      }
    });
}

/**
 * Toggle selection of a notification
 */
export function toggleSelection(notificationId, toggleCheckboxState) {
  const checkbox = document.getElementById(`select-${notificationId}`);
  if (!checkbox) return;
  if (toggleCheckboxState) {
    checkbox.checked = !checkbox.checked;
  }
  const card = checkbox.closest('.notif-item');
  if (card) {
    card.classList.toggle('selected', checkbox.checked);
  }
  updateBulkActionButtons();
}

/**
 * Select all notifications
 */
export function selectAllNotifications() {
  const checkboxes = document.querySelectorAll('#notification-list .notification-checkbox');
  checkboxes.forEach(checkbox => {
    checkbox.checked = true;
    const card = checkbox.closest('.notif-item');
    if (card) card.classList.add('selected');
  });
  updateBulkActionButtons();
}

/**
 * Deselect all notifications
 */
export function deselectAllNotifications() {
  const checkboxes = document.querySelectorAll('#notification-list .notification-checkbox');
  checkboxes.forEach(checkbox => {
    checkbox.checked = false;
    const card = checkbox.closest('.notif-item');
    if (card) card.classList.remove('selected');
  });
  updateBulkActionButtons();
}

/**
 * Update bulk action buttons visibility based on selection
 */
export function updateBulkActionButtons() {
  const allCheckboxes = document.querySelectorAll('#notification-list .notification-checkbox');
  const checkedBoxes = document.querySelectorAll('#notification-list .notification-checkbox:checked');
  const total = allCheckboxes.length;
  const selectedCount = checkedBoxes.length;

  const countEl = document.getElementById('selected-count-label') || document.querySelector('#selection-mode-bar .selected-count');
  if (countEl) {
    countEl.textContent = `${selectedCount} selected`;
  }

  const masterCheckbox = document.getElementById('select-all-checkbox');
  if (masterCheckbox) {
    if (total === 0 || selectedCount === 0) {
      masterCheckbox.checked = false;
      masterCheckbox.indeterminate = false;
    } else if (selectedCount === total) {
      masterCheckbox.checked = true;
      masterCheckbox.indeterminate = false;
    } else {
      masterCheckbox.checked = false;
      masterCheckbox.indeterminate = true;
    }
  }

  const markReadBtn = document.getElementById('btn-mark-selected-read');
  const deleteBtn = document.getElementById('btn-delete-selected');
  if (markReadBtn) markReadBtn.disabled = selectedCount === 0;
  if (deleteBtn) deleteBtn.disabled = selectedCount === 0;
}

/**
 * Toggle selection mode
 */
export function toggleSelectionMode(forceState) {
  const selectionBar = document.getElementById('selection-mode-bar');
  const toolbar = document.getElementById('notif-toolbar');
  const notifList = document.getElementById('notification-list');
  if (!selectionBar || !notifList) return;

  const willBeActive = typeof forceState === 'boolean'
    ? forceState
    : !selectionBar.classList.contains('active');

  if (!willBeActive) {
    selectionBar.classList.remove('active');
    if (toolbar) toolbar.classList.remove('hidden');
    notifList.classList.remove('selection-active');
    deselectAllNotifications();
  } else {
    selectionBar.classList.add('active');
    if (toolbar) toolbar.classList.add('hidden');
    notifList.classList.add('selection-active');
    updateBulkActionButtons();
  }
}

/**
 * Mark selected notifications as read
 */
export function markSelectedAsRead() {
  const selectedIds = [];
  document.querySelectorAll('#notification-list .notification-checkbox:checked').forEach(checkbox => {
    if (checkbox.dataset.notificationId) {
      selectedIds.push(checkbox.dataset.notificationId);
    }
  });
  
  if (selectedIds.length === 0) return;
  
  fetch('/notifications/bulk-action/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCsrfToken()
    },
    body: JSON.stringify({
      action: 'mark_read',
      notification_ids: selectedIds
    })
  }).then(response => response.json())
    .then(data => {
      if (data && data.success) {
        selectedIds.forEach(id => {
          const card = document.getElementById(`notification-${id}`);
          if (card) {
            card.classList.remove('unread-notification', 'selected');
            card.classList.add('read-notification');
            card.style.cursor = 'default';
            card.removeAttribute('hx-post');
          }
        });
        toggleSelectionMode(false);
        document.body.dispatchEvent(new CustomEvent('updateUnreadCount'));
      }
    });
}

/**
 * Delete selected notifications
 */
export function deleteSelected() {
  const selectedIds = [];
  document.querySelectorAll('#notification-list .notification-checkbox:checked').forEach(checkbox => {
    if (checkbox.dataset.notificationId) {
      selectedIds.push(checkbox.dataset.notificationId);
    }
  });
  
  if (selectedIds.length === 0) return;
  
  fetch('/notifications/bulk-action/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCsrfToken()
    },
    body: JSON.stringify({
      action: 'delete',
      notification_ids: selectedIds
    })
  }).then(response => response.json())
    .then(data => {
      if (data && data.success) {
        selectedIds.forEach(id => {
          const card = document.getElementById(`notification-${id}`);
          if (card) {
            const section = card.closest('.time-section');
            card.remove();
            if (section && !section.querySelector('.notif-item')) {
              section.remove();
            }
          }
        });
        toggleSelectionMode(false);
        const cardBody = document.querySelector('#notification-list .card-body');
        if (cardBody && !cardBody.querySelector('.notif-item, .sender-group-item')) {
          cardBody.innerHTML = '<div class="empty-state"><div class="empty-title">No notifications</div><p class="empty-sub">You\'re all caught up.</p></div>';
        }
        document.body.dispatchEvent(new CustomEvent('updateUnreadCount'));
      }
    });
}

/**
 * Get CSRF token from DOM input, meta tag, or cookie
 */
function getCsrfToken() {
  const input = document.querySelector('[name="csrfmiddlewaretoken"]');
  if (input && input.value) return input.value;
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta && meta.content) return meta.content;
  const cookies = document.cookie.split(';');
  for (let cookie of cookies) {
    const [name, value] = cookie.trim().split('=');
    if (name === 'csrftoken') {
      return decodeURIComponent(value);
    }
  }
  return '';
}

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initNotificationCenter);
} else {
  initNotificationCenter();
}
