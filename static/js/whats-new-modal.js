/**
 * vasto "What's New" Modal
 * Shows release notes for unviewed releases to users.
 * This is separate from the update manager - it shows release notes even when no update is needed.
 */

(function() {
    'use strict';

    // Check for unviewed releases on page load
    async function checkForUnviewedReleases() {
        try {
            // Only check for authenticated users
            if (!window.isAuthenticated) {
                return;
            }

            // Fetch unviewed releases
            const response = await fetch('/api/user-release-views/unviewed/');
            if (!response.ok) {
                return;
            }

            const data = await response.json();
            
            // If there are unviewed releases, check if we should auto-show modal
            if (data && data.length > 0) {
                const latestRelease = data[0]; // Already ordered by build_number desc
                
                // Fetch full release details to check type and mandatory status
                const detailResponse = await fetch(`/api/releases/${latestRelease.id}/`);
                if (!detailResponse.ok) {
                    return;
                }
                
                const releaseData = await detailResponse.json();
                
                // Auto-show modal only for mandatory updates or major releases
                const shouldAutoShow = releaseData.mandatory_update || releaseData.release_type === 'MAJOR';
                
                if (shouldAutoShow) {
                    showWhatsNewModal(releaseData, true);
                }
                // For other releases, users will see the notification with "See What's New" button
            }
        } catch (error) {
            console.warn('[WhatsNewModal] Failed to check for unviewed releases:', error);
        }
    }

    // Fetch release details and show modal
    async function fetchReleaseDetailsAndShowModal(releaseId) {
        try {
            const response = await fetch(`/api/releases/${releaseId}/`);
            if (!response.ok) {
                return;
            }

            const releaseData = await response.json();
            console.log('[WhatsNewModal] Release data received:', releaseData);
            showWhatsNewModal(releaseData, false);
        } catch (error) {
            console.warn('[WhatsNewModal] Failed to fetch release details:', error);
        }
    }

    // Show "What's New" modal
    function showWhatsNewModal(releaseData, isAutoShow = false) {
        console.log('[WhatsNewModal] showWhatsNewModal called with:', releaseData, 'isAutoShow:', isAutoShow);
        
        // Check if modal already exists
        if (document.getElementById('whats-new-modal')) {
            console.log('[WhatsNewModal] Modal already exists, skipping');
            return;
        }

        // Only check dismissal for auto-show modals, not user-triggered ones
        if (isAutoShow) {
            const dismissed = sessionStorage.getItem(`whats-new-dismissed-${releaseData.id}`);
            if (dismissed) {
                console.log('[WhatsNewModal] Release already dismissed, skipping auto-show');
                return;
            }
        } else {
            console.log('[WhatsNewModal] User-triggered modal, bypassing dismissal check');
        }

        // Create modal with people-modal style for mobile bottom sheet
        const modal = document.createElement('div');
        modal.id = 'whats-new-modal';
        modal.className = 'modal fade people-modal';
        modal.setAttribute('tabindex', '-1');
        modal.setAttribute('aria-hidden', 'true');
        console.log('[WhatsNewModal] Modal element created');
        
        // Build release items HTML with images
        let releaseItemsHtml = '';
        if (releaseData.items && releaseData.items.length > 0) {
            // Group items by category
            const itemsByCategory = {};
            releaseData.items.forEach(item => {
                if (!itemsByCategory[item.category]) {
                    itemsByCategory[item.category] = [];
                }
                itemsByCategory[item.category].push(item);
            });

            // Build HTML for each category
            for (const [category, items] of Object.entries(itemsByCategory)) {
                releaseItemsHtml += `
                    <h6 class="fw-bold mt-3 mb-2">${category}</h6>
                    <ul class="list-unstyled mb-0">
                `;
                items.forEach(item => {
                    let imagesHtml = '';
                    if (item.images && item.images.length > 0) {
                        imagesHtml = '<div class="mt-2">';
                        item.images.forEach(image => {
                            imagesHtml += `
                                <div class="mb-2">
                                    <img src="${image.image_url}" alt="${image.caption || item.title}" 
                                         class="img-fluid rounded w-100" style="max-height: 300px; object-fit: cover;" loading="lazy">
                                    ${image.caption ? `<p class="text-muted small mb-0 mt-1">${image.caption}</p>` : ''}
                                </div>
                            `;
                        });
                        imagesHtml += '</div>';
                    }
                    
                    releaseItemsHtml += `
                        <li class="mb-3">
                            <strong>${item.title}</strong>
                            ${item.description ? `<p class="text-muted small mb-1">${item.description}</p>` : ''}
                            ${imagesHtml}
                        </li>
                    `;
                });
                releaseItemsHtml += `</ul>`;
            }
        } else {
            releaseItemsHtml = '<p class="text-muted mb-0">No release notes available.</p>';
        }

        modal.innerHTML = `
            <div class="modal-dialog modal-dialog-scrollable modal-dialog-centered">
                <div class="modal-content" style="box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);">
                    <!-- Sticky Header -->
                    <div class="modal-header people-modal-header border-0 pb-0 sticky-top" style="position: relative;">
                        <div class="people-modal-handle d-md-none" aria-hidden="true"></div>
                        <h5 class="modal-title fw-bold w-100 text-center pt-2">
                            <i aria-hidden="true" data-lucide="star" class="vasto-icon vasto-icon--filled me-2 text-warning"></i>What's New
                        </h5>
                        <button type="button" class="btn-close people-modal-close" data-bs-dismiss="modal" aria-label="Close" style="position: absolute; right: 12px; top: 8px; z-index: 10; font-size: 1.25rem; opacity: 1; background-image: none;">✕</button>
                    </div>
                    
                    <!-- Scrollable Content -->
                    <div class="modal-body people-modal-body pt-2">
                        <div class="text-center mb-4">
                            <h4 class="fw-bold">Version ${releaseData.version}</h4>
                            <p class="text-muted mb-0">Build ${releaseData.build_number}</p>
                            <span class="badge bg-info">${releaseData.release_channel}</span>
                        </div>
                        
                        <h6 class="fw-bold mb-2">${releaseData.release_title}</h6>
                        <p class="text-muted mb-3">${releaseData.release_summary}</p>
                        
                        <div class="card bg-light">
                            <div class="card-body">
                                ${releaseItemsHtml}
                            </div>
                        </div>
                        
                        ${releaseData.mandatory_update ? `
                        <div class="alert alert-warning mt-3 mb-0">
                            <i aria-hidden="true" data-lucide="triangle-alert" class="vasto-icon me-2"></i>
                            This is a mandatory update. Please update to continue using vasto.
                        </div>
                        ` : ''}
                        
                        <div class="mt-4">
                            <a href="${releaseData.id ? `/system/releases/${releaseData.id}/` : '#'}" 
                               class="btn btn-outline-secondary w-100" target="_blank">
                                <i aria-hidden="true" data-lucide="info" class="vasto-icon me-2"></i>View Full Details
                            </a>
                        </div>
                    </div>
                    
                    <!-- Sticky Footer -->
                    <div class="modal-footer border-0 pt-0 pb-3">
                        <button type="button" class="btn btn-primary w-100" id="whats-new-close-btn">
                            Got It
                        </button>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        // Initialize Bootstrap modal
        const bootstrapModal = new bootstrap.Modal(modal);
        bootstrapModal.show();

        // Add event listener for close button
        document.getElementById('whats-new-close-btn').addEventListener('click', async function() {
            bootstrapModal.hide();
            modal.remove();
            
            // Mark as viewed
            await markReleaseAsViewed(releaseData.id);
            
            // Store dismissal in session storage
            sessionStorage.setItem(`whats-new-dismissed-${releaseData.id}`, 'true');
        });

        // Also mark as viewed when modal is closed via X button or backdrop
        modal.addEventListener('hidden.bs.modal', async function() {
            modal.remove();
            await markReleaseAsViewed(releaseData.id);
            sessionStorage.setItem(`whats-new-dismissed-${releaseData.id}`, 'true');
        });
    }

    // Mark release as viewed
    async function markReleaseAsViewed(releaseId) {
        try {
            await fetch('/api/user-release-views/mark_viewed/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken()
                },
                body: JSON.stringify({ release_id: releaseId })
            });
        } catch (error) {
            console.warn('[WhatsNewModal] Failed to mark release as viewed:', error);
        }
    }

    // Get CSRF token
    function getCsrfToken() {
        const cookies = document.cookie.split(';');
        for (let cookie of cookies) {
            const [name, value] = cookie.trim().split('=');
            if (name === 'csrftoken') {
                return decodeURIComponent(value);
            }
        }
        return '';
    }

    // Initialize on page load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            // Delay check slightly to not interfere with initial page load
            setTimeout(checkForUnviewedReleases, 3000);
        });
    } else {
        setTimeout(checkForUnviewedReleases, 3000);
    }

    // Expose API for manual checks
    window.vastoWhatsNew = {
        checkForUnviewedReleases,
        showWhatsNewModal: fetchReleaseDetailsAndShowModal,
        showReleaseById: fetchReleaseDetailsAndShowModal
    };
    
    // Global function for notification card buttons
    window.openWhatsNewModal = function(releaseId, event) {
        console.log('[WhatsNewModal] openWhatsNewModal called with:', releaseId);
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }
        fetchReleaseDetailsAndShowModal(releaseId);
    };
    
    console.log('[WhatsNewModal] Script loaded, window.openWhatsNewModal available:', typeof window.openWhatsNewModal);

})();
