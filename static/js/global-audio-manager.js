/**
 * Global Audio Manager for vasto
 * Manages global audio preference for video playback
 * Handles mute/unmute functionality and tutorial bubble
 */

(function() {
    'use strict';

    // ============================================================================
    // STATE MANAGEMENT
    // ============================================================================

    const state = {
        audioPreference: 'muted', // 'muted' or 'unmuted'
        tutorialSeen: false,
        isInitialized: false
    };

    const STORAGE_KEYS = {
        AUDIO_PREFERENCE: 'vasto_audio_preference',
        TUTORIAL_SEEN: 'vasto_video_tutorial_seen'
    };

    // Get user-specific storage key
    function getUserStorageKey(baseKey) {
        // Try to get username from page or use generic key for non-authenticated users
        const username = window.vastoUsername || '';
        return username ? `${baseKey}_${username}` : baseKey;
    }

    // ============================================================================
    // INITIALIZATION
    // ============================================================================

    function init() {
        if (state.isInitialized) return;

        // Load preferences from localStorage
        loadPreferences();

        // Create global mute button
        createGlobalMuteButton();

        // Create tutorial bubble
        createTutorialBubble();

        // Apply audio preference to existing videos
        applyAudioPreference();

        // Setup video observer for new videos
        setupVideoObserver();

        // Check for first video encounter to show tutorial
        checkFirstVideoEncounter();

        state.isInitialized = true;
        console.log('[GlobalAudioManager] Initialized');
    }

    function loadPreferences() {
        // First try to get preference from server if authenticated
        const userAudioPref = window.vastoUserAudioPreference;
        if (userAudioPref) {
            state.audioPreference = userAudioPref;
        } else {
            // Fallback to localStorage with user-specific key
            state.audioPreference = localStorage.getItem(getUserStorageKey(STORAGE_KEYS.AUDIO_PREFERENCE)) || 'muted';
        }
        state.tutorialSeen = localStorage.getItem(getUserStorageKey(STORAGE_KEYS.TUTORIAL_SEEN)) === 'true';
    }

    // ============================================================================
    // GLOBAL MUTE BUTTON
    // ============================================================================

    function createGlobalMuteButton() {
        // Find the theme toggle dropdown to place button before it
        const themeDropdown = document.querySelector('.dropdown .dropdown-menu .dropdown-item[onclick*="setTheme"]')?.closest('.dropdown');
        if (!themeDropdown) {
            console.warn('[GlobalAudioManager] Theme toggle not found, appending to header');
            const header = document.querySelector('.navbar-top .d-flex.align-items-center.gap-3');
            if (header) {
                header.insertAdjacentHTML('beforeend', createMuteButtonHTML());
            }
        } else {
            // Insert before the theme dropdown
            themeDropdown.insertAdjacentHTML('beforebegin', createMuteButtonHTML());
        }

        // Setup button event listener
        const muteButton = document.getElementById('global-mute-button');
        if (muteButton) {
            muteButton.addEventListener('click', toggleGlobalAudio);
        }
    }

    function createMuteButtonHTML() {
        const icon = state.audioPreference === 'muted' ? 'bi-volume-mute' : 'bi-volume-up';
        return `
            <button id="global-mute-button" 
                    class="btn btn-link p-0 position-relative" 
                    style="color: var(--text-dark);"
                    title="${state.audioPreference === 'muted' ? 'Unmute all videos' : 'Mute all videos'}">
                <i class="bi ${icon}" style="font-size: 1.2rem;"></i>
            </button>
        `;
    }

    function toggleGlobalAudio() {
        // Toggle preference
        state.audioPreference = state.audioPreference === 'muted' ? 'unmuted' : 'muted';
        
        // Save to localStorage with user-specific key
        localStorage.setItem(getUserStorageKey(STORAGE_KEYS.AUDIO_PREFERENCE), state.audioPreference);
        
        // Save to server if authenticated
        saveAudioPreferenceToServer();
        
        // Update button icon
        updateMuteButtonIcon();
        
        // Apply to all videos
        applyAudioPreference();

        // Also sync with VideoManager if active
        try {
            if (window.videoManager && typeof window.videoManager.setGlobalMute === 'function') {
                window.videoManager.setGlobalMute(state.audioPreference === 'muted');
            } else if (window.vastoVideoManager && typeof window.vastoVideoManager.setGlobalMute === 'function') {
                window.vastoVideoManager.setGlobalMute(state.audioPreference === 'muted');
            }
        } catch (_) {}
        
        console.log('[GlobalAudioManager] Audio preference toggled:', state.audioPreference);
    }

    function saveAudioPreferenceToServer() {
        // Only save if user is authenticated
        if (window.isAuthenticated) {
            fetch('/users/api/users/update_audio_preference/', {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken()
                },
                body: JSON.stringify({ audio_preference: state.audioPreference })
            })
            .then(response => {
                if (response.ok) {
                    console.log('[GlobalAudioManager] Audio preference saved to server');
                } else {
                    console.warn('[GlobalAudioManager] Failed to save audio preference to server');
                }
            })
            .catch(error => {
                console.error('[GlobalAudioManager] Error saving audio preference:', error);
            });
        }
    }

    function getCsrfToken() {
        // Get CSRF token from cookie or meta tag
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
        if (csrfToken) return csrfToken;
        
        // Fallback to getting from cookie
        const cookies = document.cookie.split(';');
        for (let cookie of cookies) {
            const [name, value] = cookie.trim().split('=');
            if (name === 'csrftoken') return decodeURIComponent(value);
        }
        return '';
    }

    function updateMuteButtonIcon() {
        const muteButton = document.getElementById('global-mute-button');
        if (muteButton) {
            const icon = muteButton.querySelector('i');
            const newIcon = state.audioPreference === 'muted' ? 'bi-volume-mute' : 'bi-volume-up';
            icon.className = `bi ${newIcon}`;
            muteButton.title = state.audioPreference === 'muted' ? 'Unmute all videos' : 'Mute all videos';
        }
    }

    // ============================================================================
    // TUTORIAL BUBBLE
    // ============================================================================

    function createTutorialBubble() {
        const muteButton = document.getElementById('global-mute-button');
        if (!muteButton) return;

        // Create tutorial bubble
        const bubble = document.createElement('div');
        bubble.id = 'audio-tutorial-bubble';
        bubble.className = 'audio-tutorial-bubble';
        bubble.innerHTML = `
            <div class="bubble-content">
                <p class="bubble-text">Use this button to mute/unmute all videos</p>
                <button class="bubble-close" onclick="dismissTutorial()">
                    <i class="bi bi-x-lg"></i>
                </button>
            </div>
            <div class="bubble-tail"></div>
        `;

        // Style the bubble
        const style = document.createElement('style');
        style.textContent = `
            .audio-tutorial-bubble {
                position: absolute;
                top: 100%;
                right: 0;
                margin-top: 10px;
                z-index: 10000;
                opacity: 0;
                pointer-events: none;
                transition: opacity 0.3s ease;
                width: 200px;
            }
            
            .audio-tutorial-bubble.show {
                opacity: 1;
                pointer-events: auto;
            }
            
            .bubble-content {
                background: var(--card-bg);
                border: 1px solid var(--border);
                border-radius: 8px;
                padding: 12px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
                position: relative;
            }
            
            .bubble-text {
                margin: 0;
                font-size: 13px;
                color: var(--text-dark);
                line-height: 1.4;
            }
            
            .bubble-close {
                position: absolute;
                top: 4px;
                right: 4px;
                background: none;
                border: none;
                color: var(--text-secondary);
                cursor: pointer;
                padding: 4px;
                border-radius: 4px;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            
            .bubble-close:hover {
                background: var(--border);
            }
            
            .bubble-tail {
                position: absolute;
                top: -8px;
                right: 20px;
                width: 0;
                height: 0;
                border-left: 8px solid transparent;
                border-right: 8px solid transparent;
                border-bottom: 8px solid var(--border);
            }
            
            .bubble-tail::after {
                content: '';
                position: absolute;
                top: 1px;
                left: -7px;
                width: 0;
                height: 0;
                border-left: 7px solid transparent;
                border-right: 7px solid transparent;
                border-bottom: 7px solid var(--card-bg);
            }
            
            /* Desktop hover behavior after tutorial seen */
            @media (hover: hover) {
                #global-mute-button:hover .audio-tutorial-bubble {
                    opacity: 1;
                    pointer-events: auto;
                }
            }
            
            /* Mobile: no hover behavior */
            @media (hover: none) {
                #global-mute-button:hover .audio-tutorial-bubble {
                    opacity: 0;
                    pointer-events: none;
                }
            }
        `;
        document.head.appendChild(style);

        // Position bubble relative to mute button
        muteButton.style.position = 'relative';
        muteButton.appendChild(bubble);
    }

    function showTutorial() {
        const bubble = document.getElementById('audio-tutorial-bubble');
        if (bubble) {
            bubble.classList.add('show');
            
            // Auto-hide after 5 seconds
            setTimeout(() => {
                dismissTutorial();
            }, 5000);
        }
    }

    function dismissTutorial() {
        const bubble = document.getElementById('audio-tutorial-bubble');
        if (bubble) {
            bubble.classList.remove('show');
        }
        
        // Mark as seen with user-specific key
        state.tutorialSeen = true;
        localStorage.setItem(getUserStorageKey(STORAGE_KEYS.TUTORIAL_SEEN), 'true');
        
        console.log('[GlobalAudioManager] Tutorial dismissed');
    }

    // Make dismissTutorial available globally
    window.dismissTutorial = dismissTutorial;

    // ============================================================================
    // VIDEO AUDIO MANAGEMENT
    // ============================================================================

    function isExcludedVideo(video) {
        if (!video) return true;
        if (video.dataset && (
            video.dataset.chatMedia === 'true' ||
            video.dataset.noInline === 'true' ||
            video.dataset.messaging === 'true'
        )) {
            return true;
        }
        // The blurred letterbox filler behind a fullscreen reel is a silent
        // copy of the reel itself — it must never be unmuted.
        if (video.classList && video.classList.contains('reel-ambient-video')) {
            return true;
        }
        if (video.closest('.media-viewer-overlay, #messagesContainer, .messages-area, .media-bubble, .media-tile, .message-bubble, .message-wrapper, .conversation-container, #chatMessages, #chatAppContainer, .messaging-view, #mediaPreviewModal, .media-preview-modal, .media-composer-modal, .reels-carousel-shelf')) {
            return true;
        }
        return false;
    }

    function applyAudioPreference() {
        const videos = document.querySelectorAll('video');
        videos.forEach(video => {
            if (isExcludedVideo(video)) return;
            if (state.audioPreference === 'unmuted') {
                video.muted = false;
            } else {
                video.muted = true;
            }
        });
    }

    function setupVideoObserver() {
        // Watch for new videos being added to the page
        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeType === Node.ELEMENT_NODE) {
                        if (node.tagName === 'VIDEO' || node.querySelector('video')) {
                            // Apply audio preference to new videos
                            setTimeout(() => applyAudioPreference(), 100);
                        }
                    }
                });
            });
        });

        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    }

    // ============================================================================
    // FIRST VIDEO ENCOUNTER DETECTION
    // ============================================================================

    function checkFirstVideoEncounter() {
        // Check if tutorial has been seen
        if (state.tutorialSeen) return;

        // Check if there are videos on the page
        const videos = document.querySelectorAll('video');
        if (videos.length > 0) {
            // Show tutorial
            showTutorial();
        } else {
            // Watch for videos to be added
            const observer = new MutationObserver((mutations) => {
                mutations.forEach((mutation) => {
                    mutation.addedNodes.forEach((node) => {
                        if (node.nodeType === Node.ELEMENT_NODE) {
                            if (node.tagName === 'VIDEO' || node.querySelector('video')) {
                                // First video encountered
                                if (!state.tutorialSeen) {
                                    showTutorial();
                                    observer.disconnect();
                                }
                            }
                        }
                    });
                });
            });

            observer.observe(document.body, {
                childList: true,
                subtree: true
            });
        }
    }

    // ============================================================================
    // PUBLIC API
    // ============================================================================

    window.vastoGlobalAudio = {
        init,
        toggleGlobalAudio,
        getAudioPreference: () => state.audioPreference,
        setAudioPreference: (preference) => {
            state.audioPreference = preference;
            localStorage.setItem(getUserStorageKey(STORAGE_KEYS.AUDIO_PREFERENCE), preference);
            localStorage.setItem(STORAGE_KEYS.AUDIO_PREFERENCE, preference);
            updateMuteButtonIcon();
            applyAudioPreference();
            try {
                if (window.videoManager && typeof window.videoManager.setGlobalMute === 'function') {
                    if (window.videoManager.isGlobalMuted !== (preference === 'muted')) {
                        window.videoManager.setGlobalMute(preference === 'muted');
                    }
                } else if (window.vastoVideoManager && typeof window.vastoVideoManager.setGlobalMute === 'function') {
                    if (window.vastoVideoManager.isGlobalMuted !== (preference === 'muted')) {
                        window.vastoVideoManager.setGlobalMute(preference === 'muted');
                    }
                }
            } catch (_) {}
        }
    };

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
