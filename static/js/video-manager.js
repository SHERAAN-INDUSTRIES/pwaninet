/**
 * vasto Unified Video Manager (Phase 4)
 * 
 * Features:
 * - Single feed IntersectionObserver (threshold: 0.65)
 * - Strict playback policy:
 *     * Autoplays portrait reels (>=65% visible), exactly one playing at any time
 *     * Landscape videos require manual play tap and pause active reels
 * - Global mute state synchronized across all feed & fullscreen videos
 * - Delegated single tap (play/pause + transient HUD) vs. double tap (heart burst + like)
 * - Progressive vertical video promotion (legacy backfill fallback)
 * - Dedicated Full-Screen Reels Overlay Mode with vertical 100dvh snap-scrolling
 * - Full HTMX lifecycle integration with memory leak prevention
 */

(function() {
    'use strict';

    // ============================================================================
    // STATE & CONFIGURATION
    // ============================================================================

    const username = window.vastoUsername || '';
    const storageKey = username ? `vasto_audio_preference_${username}` : 'vasto_audio_preference';
    let isGlobalMuted = (localStorage.getItem(storageKey) || window.vastoUserAudioPreference || 'muted') === 'muted';
    let isProgrammaticAudioSync = false;

    // Auto-scroll preference (persisted per user)
    const autoScrollKey = username ? `vasto_autoscroll_${username}` : 'vasto_autoscroll';
    let isAutoScrollEnabled = localStorage.getItem(autoScrollKey) === 'true';

    // Auto-scroll prompt nudge — tracks when to show, snooze, and when to give up
    const promptTsKey    = username ? `vasto_autoscroll_prompt_ts_${username}`    : 'vasto_autoscroll_prompt_ts';
    const promptCountKey = username ? `vasto_autoscroll_prompt_count_${username}` : 'vasto_autoscroll_prompt_count';
    const PROMPT_SNOOZE_MS    = 7 * 24 * 60 * 60 * 1000; // 7 days
    const PROMPT_MAX_DISMISSALS = 3;                       // stop asking after 3 dismissals
    const PROMPT_TRIGGER_REELS  = 3;                       // show after watching N reels in the session
    let   _promptSessionReelCount = 0;                     // how many reels played this session
    let   _promptShownThisSession = false;                  // show at most once per session

    const state = {
        videos: new Map(), // video element -> video metadata
        feedObserver: null,
        fullscreenObserver: null,
        mutationObserver: null,
        currentPlayingVideo: null,
        isFullScreenActive: false,
        isClosingFullscreen: false,
        fullscreenReturnFocus: null,
        previousScrollY: 0,
        tapTimers: new Map(), // element -> timer id
        hasLongPressed: false,
        isInitialized: false
    };

    // Virtualized Three-Slot Playback Engine State (Phase 1)
    const reelSlotEngine = {
        activeIndex: -1,
        previousIndex: -1,
        nextIndex: -1,
        suspendedFeedVideos: []
    };

    // Global Gesture Discrimination State (Phase 2: Prevents Swipes Turning into Clicks)
    let isGestureDrag = false;

    // ============================================================================
    // UTILITY HELPERS
    // ============================================================================

    function extractPostId(element) {
        if (!element) return null;
        if (element.dataset && element.dataset.postId) {
            const id = String(element.dataset.postId).trim();
            if (id && id !== 'null' && id !== 'undefined') return id;
        }
        if (element.id) {
            if (element.id.startsWith('post-card-')) {
                const id = element.id.replace('post-card-', '').trim();
                if (id && id !== 'null' && id !== 'undefined') return id;
            }
            if (element.id.startsWith('video-')) {
                const id = element.id.replace('video-', '').trim();
                if (id && id !== 'null' && id !== 'undefined') return id;
            }
            if (element.id.startsWith('fs-video-')) {
                const id = element.id.replace('fs-video-', '').trim();
                if (id && id !== 'null' && id !== 'undefined') return id;
            }
            if (element.id.startsWith('reel-card-')) {
                const id = element.id.replace('reel-card-', '').trim();
                if (id && id !== 'null' && id !== 'undefined') return id;
            }
            if (element.id.startsWith('reel-hitbox-')) {
                const id = element.id.replace('reel-hitbox-', '').trim();
                if (id && id !== 'null' && id !== 'undefined') return id;
            }
        }
        const parent = element.closest('[data-post-id]');
        if (parent && parent.dataset && parent.dataset.postId) {
            const id = String(parent.dataset.postId).trim();
            if (id && id !== 'null' && id !== 'undefined') return id;
        }
        return null;
    }

    function isReelElement(video) {
        if (!video) return false;
        if (video.classList.contains('reel-video-element') || video.classList.contains('fullscreen-reel-video')) return true;
        if (video.closest('.reel-card-container, .reel-post-card, .reel-stage-container') || video.closest('.reels-snap-item')) return true;
        if (video.dataset && video.dataset.reel === 'true') return true;
        return false;
    }

    function isLandscapeVideo(video) {
        if (!video) return false;
        if (video.closest('.landscape-video-container')) return true;
        return !isReelElement(video);
    }

    // ============================================================================
    // REEL ENGAGEMENT & SHARING SYNC (LIKES, REPOSTS, EXTERNAL AUTO-LAUNCH)
    // ============================================================================

    function getCsrfToken(formEl) {
        // 1. Try input inside the specific active form if provided
        if (formEl && formEl.querySelector) {
            const input = formEl.querySelector('[name=csrfmiddlewaretoken]');
            if (input && input.value) return input.value;
        }

        // 2. Try the meta tag (rendered by Django {{ csrf_token }})
        const metaTag = document.querySelector('meta[name="csrf-token"]');
        if (metaTag && metaTag.getAttribute('content')) {
            const val = metaTag.getAttribute('content').trim();
            if (val && !val.includes('{{')) return val;
        }

        // 3. Fallback to active form or any csrfmiddlewaretoken input in DOM
        const formInput = (document.activeElement && document.activeElement.closest('form')?.querySelector('[name=csrfmiddlewaretoken]')) ||
                          document.getElementById('reelCommentForm')?.querySelector('[name=csrfmiddlewaretoken]') ||
                          document.getElementById('fsRailCommentForm')?.querySelector('[name=csrfmiddlewaretoken]') ||
                          document.querySelector('[name=csrfmiddlewaretoken]');
        if (formInput && formInput.value) return formInput.value;

        // 4. Try reading the active csrf cookie (prefer local configured name vasto_local_csrftoken, fallback to csrftoken)
        try {
            if (document.cookie) {
                const cookies = document.cookie.split(';');
                let localToken = '';
                let defaultToken = '';
                for (const cookie of cookies) {
                    const [name, value] = cookie.trim().split('=');
                    if (name === 'vasto_local_csrftoken') {
                        localToken = decodeURIComponent(value || '');
                    } else if (name === 'csrftoken') {
                        defaultToken = decodeURIComponent(value || '');
                    }
                }
                if (localToken) return localToken;
                if (defaultToken) return defaultToken;
            }
        } catch (_) {}

        return '';
    }

    function syncLikeUiAcrossSite(postId, isLiked, likeCount) {
        if (!postId) return;
        postId = String(postId).trim();

        // 1. Update fullscreen snap items
        const snapItems = document.querySelectorAll(`.reels-snap-item[data-post-id="${postId}"]`);
        snapItems.forEach(snapItem => {
            if (isLiked !== undefined && isLiked !== null) {
                snapItem.dataset.isLiked = isLiked ? 'true' : 'false';
            }
            if (likeCount !== undefined && likeCount !== null) {
                snapItem.dataset.likes = likeCount;
            }
            const proxyBtn = snapItem.querySelector('.fs-like-proxy-btn');
            if (proxyBtn && isLiked !== undefined && isLiked !== null) {
                proxyBtn.classList.toggle('liked', isLiked);
                proxyBtn.classList.toggle('text-danger', isLiked);
                const icon = proxyBtn.querySelector('i');
                if (icon) icon.className = isLiked ? 'bi bi-heart-fill text-danger' : 'bi bi-heart';
            }
            const countEl = snapItem.querySelector(`#fs-reel-like-count-${postId}`);
            if (countEl && likeCount !== undefined && likeCount !== null) {
                countEl.textContent = likeCount;
            }
        });

        // 2. Update desktop exterior engagement rail
        const desktopLikeBtn = document.getElementById('fsDesktopLikeBtn');
        const desktopLikeCount = document.getElementById('fsDesktopLikeCount');
        if (desktopLikeBtn && (desktopLikeBtn.dataset.postId === postId || currentDesktopRailPostId === postId)) {
            if (isLiked !== undefined && isLiked !== null) {
                desktopLikeBtn.classList.toggle('liked', isLiked);
                desktopLikeBtn.classList.toggle('text-danger', isLiked);
                const icon = desktopLikeBtn.querySelector('i');
                if (icon) icon.className = isLiked ? 'bi bi-heart-fill text-danger' : 'bi bi-heart';
            }
        }
        if (desktopLikeCount && (desktopLikeBtn?.dataset.postId === postId || currentDesktopRailPostId === postId)) {
            if (likeCount !== undefined && likeCount !== null) {
                desktopLikeCount.textContent = likeCount;
            }
        }

        // 3. Update all feed cards and carousel cards
        const cardSelectors = [
            `#post-card-${postId}`,
            `#reel-card-${postId}`,
            `.post-card[data-post-id="${postId}"]`,
            `.reel-post-card[data-post-id="${postId}"]`,
            `.reels-carousel-card[data-post-id="${postId}"]`
        ];
        document.querySelectorAll(cardSelectors.join(', ')).forEach(card => {
            if (isLiked !== undefined && isLiked !== null) {
                card.setAttribute('data-is-liked', isLiked ? 'true' : 'false');
            }
            if (likeCount !== undefined && likeCount !== null) {
                card.setAttribute('data-likes', likeCount);
            }
        });

        // 4. Update inline feed like buttons
        const btnSelectors = [
            `.like-button[data-share-id="${postId}"]`,
            `.like-button[data-post-id="${postId}"]`,
            `#reel-like-wrap-${postId} .like-button`,
            `#post-card-${postId} .like-button`
        ];
        document.querySelectorAll(btnSelectors.join(', ')).forEach(btn => {
            const isReel = btn.classList.contains('reel-action-btn');
            const icon = btn.querySelector('i');
            if (isLiked !== undefined && isLiked !== null) {
                if (isReel) {
                    btn.classList.toggle('liked', isLiked);
                    btn.classList.toggle('text-danger', isLiked);
                    btn.classList.toggle('text-white', !isLiked);
                    if (icon) icon.className = isLiked ? 'bi bi-heart-fill text-danger' : 'bi bi-heart';
                } else {
                    btn.classList.toggle('text-danger', isLiked);
                    btn.classList.toggle('text-muted', !isLiked);
                    if (icon) icon.className = isLiked ? 'bi bi-heart-fill text-danger heart-pop fs-5' : 'bi bi-heart fs-5';
                }
            }
            const countSpan = btn.querySelector('.like-count') || document.getElementById(`reel-like-count-${postId}`) || btn.querySelector('span');
            if (countSpan && likeCount !== undefined && likeCount !== null) {
                countSpan.textContent = likeCount;
            }
        });
    }

    // In-flight like toggles per post — prevents double-tap / rapid clicks
    // from firing two server toggles (the server toggle is a real toggle, so
    // two requests = like added then removed).
    const pendingLikeToggles = new Set();

    function toggleReelLike(postId) {
        if (!postId) return;
        postId = String(postId).trim();
        if (pendingLikeToggles.has(postId)) return;
        pendingLikeToggles.add(postId);

        const snapItem = document.querySelector(`.reels-snap-item[data-post-id="${postId}"]`);
        const fsLikeBtn = snapItem?.querySelector('.fs-like-proxy-btn') || document.querySelector(`.fs-like-proxy-btn[data-post-id="${postId}"]`);
        const isCurrentlyLiked = snapItem ? (snapItem.dataset.isLiked === 'true') : (fsLikeBtn?.classList.contains('liked') || false);
        const willBeLiked = !isCurrentlyLiked;

        let currentCount = parseInt(snapItem?.dataset?.likes || document.getElementById(`fs-reel-like-count-${postId}`)?.textContent || '0', 10) || 0;
        let optimisticCount = willBeLiked ? currentCount + 1 : Math.max(0, currentCount - 1);

        syncLikeUiAcrossSite(postId, willBeLiked, optimisticCount);

        const csrfToken = getCsrfToken();
        fetch(`/like/${postId}/?format=json`, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken,
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({})
        })
        .then(res => res.json())
        .then(data => {
            if (data && data.status === 'success') {
                syncLikeUiAcrossSite(postId, data.is_liked, data.like_count);
            }
        })
        .catch(err => {
            console.warn('[VideoManager] Like toggle error, reverting:', err);
            syncLikeUiAcrossSite(postId, isCurrentlyLiked, currentCount);
        })
        .finally(() => {
            pendingLikeToggles.delete(postId);
        });
    }

    function renderRepostScatterClusterHtml(badgeData, isFullscreen) {
        if (!badgeData || !badgeData.avatars || badgeData.avatars.length === 0) {
            return '';
        }
        const avatars = badgeData.avatars.slice(0, 2);
        const count = Number.isFinite(Number(badgeData.visible_repost_count))
            ? Number(badgeData.visible_repost_count)
            : avatars.length;

        const escapeHtml = (value) => String(value ?? '').replace(/[&<>\"']/g, char => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', "'": '&#39;'
        }[char]));

        let itemsHtml = '';
        avatars.forEach((ru) => {
            const avatarUrl = escapeHtml(ru.avatar || '/static/images/default-avatar.png');
            const username = String(ru.username || '');
            const name = escapeHtml(ru.name || username || 'User');
            const profilePath = `/users/user/${encodeURIComponent(username)}/`;
            itemsHtml += `
                <div class="scatter-item position-relative" title="${name} reposted">
                    <a href="${profilePath}"
                       hx-get="${profilePath}"
                       hx-target="#page-content-target"
                       hx-swap="innerHTML"
                       hx-push-url="true"
                       class="d-block text-decoration-none">
                        <img src="${avatarUrl}"
                             class="scatter-avatar-img"
                             alt="${username}"
                             onerror="this.src='/static/images/default-avatar.png'">
                    </a>
                    <span class="scatter-repost-icon" title="Repost">
                        <i class="bi bi-repeat"></i>
                    </span>
                </div>
            `;
        });

        let overflowHtml = '';
        if (count > avatars.length) {
            overflowHtml = `
                <span class="badge bg-dark bg-opacity-75 text-white rounded-pill px-2 py-1 ms-1 fw-semibold" title="More people you follow reposted this" aria-label="More people you follow reposted this" style="font-size: 0.72rem; backdrop-filter: blur(4px);">
                    +${count - avatars.length}
                </span>
            `;
        }

        return `
            <div class="reel-repost-scatter-cluster scatter-row d-inline-flex align-items-center">
                <div class="scatter-items-group position-relative">
                    ${itemsHtml}
                </div>
                ${overflowHtml}
            </div>
        `;
    }

    function updateRepostBadgeDom(postId, isReposted, repostCount, userProfile) {
        if (!postId) return;
        postId = String(postId).trim();

        const user = {
            id: userProfile?.user_id || userProfile?.id || document.body.dataset.userId || '',
            username: userProfile?.user_username || userProfile?.username || document.body.dataset.userUsername || '',
            name: userProfile?.user_name || userProfile?.name || document.body.dataset.userName || document.body.dataset.userUsername || 'You',
            avatar: userProfile?.user_avatar || userProfile?.avatar || document.body.dataset.userAvatar || '/static/images/default-avatar.png'
        };

        const card = document.getElementById(`reel-card-${postId}`) ||
                     document.querySelector(`.reel-post-card[data-post-id="${postId}"]`) ||
                     document.querySelector(`[data-post-id="${postId}"]`);

        let badgeData = { repost_count: 0, visible_repost_count: 0, is_reposted: false, avatars: [], followed_friend: null };
        if (card && card.dataset.repostBadge) {
            try {
                badgeData = typeof card.dataset.repostBadge === 'string'
                    ? JSON.parse(card.dataset.repostBadge)
                    : card.dataset.repostBadge;
            } catch (e) {
                badgeData = { repost_count: 0, visible_repost_count: 0, is_reposted: false, avatars: [], followed_friend: null };
            }
        }
        if (!badgeData.avatars) badgeData.avatars = [];

        const userRepostAction = (isReposted !== undefined && isReposted !== null) ? isReposted : userProfile?.is_reposted;

        if (userRepostAction !== undefined && userRepostAction !== null) {
            if (userRepostAction) {
                if (isReposted) badgeData.is_reposted = true;
                if (repostCount !== undefined && repostCount !== null) {
                    badgeData.repost_count = repostCount;
                } else {
                    badgeData.repost_count = (badgeData.repost_count || 0) + 1;
                }
            } else {
                if (isReposted === false) badgeData.is_reposted = false;
                if (repostCount !== undefined && repostCount !== null) {
                    badgeData.repost_count = repostCount;
                } else {
                    badgeData.repost_count = Math.max(0, (badgeData.repost_count || 1) - 1);
                }
            }
        } else if (repostCount !== undefined && repostCount !== null) {
            badgeData.repost_count = repostCount;
        }
        badgeData.visible_repost_count = Math.max(
            Number(badgeData.visible_repost_count) || 0,
            badgeData.avatars.length
        );

        document.querySelectorAll(`[data-post-id="${postId}"]`).forEach(c => {
            c.dataset.repostBadge = JSON.stringify(badgeData);
        });

        // 1. Update Top Text-Only Repost Banner (#fb-repost-banner-${postId}) on both Reel Cards & Post Cards
        const topBanners = document.querySelectorAll(`#fb-repost-banner-${postId}`);
        topBanners.forEach(banner => {
            const textEl = banner.querySelector('.fb-repost-banner-text');
            if (!textEl) return;
            const totalCount = Math.max(badgeData.repost_count || 0, badgeData.avatars.length);
            const friend = badgeData.followed_friend;

            if (badgeData.is_reposted) {
                let headerHtml = '<strong>You</strong>';
                if (friend && friend.username) {
                    const friendName = friend.name || friend.username;
                    const friendLink = `<a href="/users/user/${friend.username}/" hx-get="/users/user/${friend.username}/" hx-target="#page-content-target" hx-swap="innerHTML" hx-push-url="true" class="text-decoration-none text-dark fw-semibold">${friendName}</a>`;
                    const othersCount = Math.max(0, totalCount - 2);
                    if (othersCount > 0) {
                        headerHtml = `<strong>You</strong>, ${friendLink} and ${othersCount} ${othersCount === 1 ? 'other' : 'others'} reposted this`;
                    } else {
                        headerHtml = `<strong>You</strong> and ${friendLink} reposted this`;
                    }
                } else {
                    const othersCount = Math.max(0, totalCount - 1);
                    if (othersCount > 0) {
                        headerHtml = `<strong>You</strong> and ${othersCount} ${othersCount === 1 ? 'other' : 'others'} reposted this`;
                    } else {
                        headerHtml = `<strong>You</strong> reposted this`;
                    }
                }
                textEl.innerHTML = `${headerHtml} <span class="text-muted fw-normal">&middot; just now</span>`;
                banner.classList.remove('d-none');
                if (window.htmx) htmx.process(banner);
            } else if (friend && friend.username && totalCount > 0) {
                const friendName = friend.name || friend.username;
                const friendLink = `<a href="/users/user/${friend.username}/" hx-get="/users/user/${friend.username}/" hx-target="#page-content-target" hx-swap="innerHTML" hx-push-url="true" class="text-decoration-none text-dark fw-semibold">${friendName}</a>`;
                const othersCount = Math.max(0, totalCount - 1);
                let headerHtml = `${friendLink} reposted this`;
                if (othersCount > 0) {
                    headerHtml = `${friendLink} and ${othersCount} ${othersCount === 1 ? 'other' : 'others'} reposted this`;
                }
                textEl.innerHTML = `${headerHtml} <span class="text-muted fw-normal">&middot; just now</span>`;
                banner.classList.remove('d-none');
                if (window.htmx) htmx.process(banner);
            } else {
                banner.classList.add('d-none');
            }
        });

        // 2. Update Video Stage Bottom-Left Repost Cluster (Reel Cards only)
        const feedClusterWrap = document.getElementById(`reel-card-repost-cluster-${postId}`) ||
                                document.querySelector(`#reel-card-${postId} .reel-card-repost-cluster-wrap`);
        if (feedClusterWrap) {
            if (badgeData.avatars.length > 0) {
                feedClusterWrap.innerHTML = renderRepostScatterClusterHtml(badgeData, false);
                feedClusterWrap.classList.remove('d-none');
                if (window.htmx) htmx.process(feedClusterWrap);
            } else {
                feedClusterWrap.innerHTML = '';
                feedClusterWrap.classList.add('d-none');
            }
        }

        // 3. Update Fullscreen Snap Item Repost Badge
        const fsBadgeContainers = document.querySelectorAll(`#fs-repost-badge-${postId}`);
        fsBadgeContainers.forEach(fsContainer => {
            if (badgeData.avatars.length > 0) {
                fsContainer.innerHTML = renderRepostScatterClusterHtml(badgeData, true);
                fsContainer.classList.remove('d-none');
                if (window.htmx) htmx.process(fsContainer);
            } else {
                fsContainer.innerHTML = '';
                fsContainer.classList.add('d-none');
            }
        });
    }

    function syncRepostUiAcrossSite(postId, isReposted, repostCount, userProfile) {
        if (!postId) return;
        postId = String(postId).trim();

        // 1. Update fullscreen snap items
        const snapItems = document.querySelectorAll(`.reels-snap-item[data-post-id="${postId}"]`);
        snapItems.forEach(snapItem => {
            if (isReposted !== undefined && isReposted !== null) {
                snapItem.dataset.isReposted = isReposted ? 'true' : 'false';
            }
            if (repostCount !== undefined && repostCount !== null) {
                snapItem.dataset.repostCount = repostCount;
            }
            const proxyBtn = snapItem.querySelector('.fs-repost-proxy-btn');
            if (proxyBtn && isReposted !== undefined && isReposted !== null) {
                proxyBtn.classList.toggle('is-reposted', isReposted);
                proxyBtn.classList.toggle('text-primary', isReposted);
                const icon = proxyBtn.querySelector('i');
                if (icon) icon.className = isReposted ? 'bi bi-repeat text-primary' : 'bi bi-repeat';
                proxyBtn.title = isReposted ? 'Reposted' : 'Repost';
                proxyBtn.setAttribute('aria-label', isReposted ? 'Undo repost' : 'Repost');
                const status = snapItem.querySelector(`#fs-reel-repost-state-${postId}`);
                if (status) status.classList.toggle('d-none', !isReposted);
            }
            const countEl = snapItem.querySelector(`#fs-reel-repost-count-${postId}`);
            if (countEl && repostCount !== undefined && repostCount !== null) {
                countEl.textContent = repostCount;
            }
        });

        // 2. Update desktop exterior engagement rail
        const desktopRepostBtn = document.getElementById('fsDesktopRepostBtn');
        const desktopRepostCount = document.getElementById('fsDesktopRepostCount');
        if (desktopRepostBtn && (desktopRepostBtn.dataset.postId === postId || currentDesktopRailPostId === postId)) {
            if (isReposted !== undefined && isReposted !== null) {
                desktopRepostBtn.classList.toggle('is-reposted', isReposted);
                desktopRepostBtn.classList.toggle('text-primary', isReposted);
                const icon = desktopRepostBtn.querySelector('i');
                if (icon) icon.className = isReposted ? 'bi bi-repeat text-primary' : 'bi bi-repeat';
                desktopRepostBtn.title = isReposted ? 'Reposted' : 'Repost';
                desktopRepostBtn.setAttribute('aria-label', isReposted ? 'Undo repost' : 'Repost');
                document.getElementById('fsDesktopRepostState')?.classList.toggle('d-none', !isReposted);
            }
        }
        if (desktopRepostCount && (desktopRepostBtn?.dataset.postId === postId || currentDesktopRailPostId === postId)) {
            if (repostCount !== undefined && repostCount !== null) {
                desktopRepostCount.textContent = repostCount;
            }
        }

        // 3. Update global share modal repost chip if open for this post
        const modalPostId = document.getElementById('globalSharePostId')?.value;
        if (modalPostId === postId) {
            const modalRepostBtn = document.getElementById('globalShareRepostBtn');
            const modalRepostIcon = document.getElementById('globalShareRepostIcon');
            const modalRepostLabel = document.getElementById('globalShareRepostLabel');
            if (isReposted !== undefined && isReposted !== null) {
                if (modalRepostBtn) modalRepostBtn.classList.toggle('active', isReposted);
                if (modalRepostIcon) modalRepostIcon.className = isReposted ? 'bi bi-repeat fs-5 text-primary' : 'bi bi-repeat fs-5 text-dark';
                if (modalRepostLabel) modalRepostLabel.textContent = isReposted ? 'Reposted' : 'Repost';
            }
        }

        // 4. Update all feed cards and carousel cards
        const cardSelectors = [
            `#post-card-${postId}`,
            `#reel-card-${postId}`,
            `.post-card[data-post-id="${postId}"]`,
            `.reel-post-card[data-post-id="${postId}"]`,
            `.reels-carousel-card[data-post-id="${postId}"]`
        ];
        document.querySelectorAll(cardSelectors.join(', ')).forEach(card => {
            if (isReposted !== undefined && isReposted !== null) {
                card.setAttribute('data-is-reposted', isReposted ? 'true' : 'false');
            }
            if (repostCount !== undefined && repostCount !== null) {
                card.setAttribute('data-repost-count', repostCount);
            }
        });

        // 5. Update feed repost action buttons
        const repostBtnSelectors = [
            `#repost-btn-${postId}`,
            `[data-post-id="${postId}"] .repost-button`,
            `#post-card-${postId} .repost-button`,
            `#reel-card-${postId} .repost-button`
        ];
        document.querySelectorAll(repostBtnSelectors.join(', ')).forEach(btn => {
            if (isReposted !== undefined && isReposted !== null) {
                btn.classList.toggle('text-primary', isReposted);
                btn.classList.toggle('text-muted', !isReposted);
                const icon = btn.querySelector('i');
                if (icon) icon.className = isReposted ? 'bi bi-repeat fs-5 text-primary' : 'bi bi-repeat fs-5';
                const stateLabel = btn.querySelector('.repost-state-label');
                if (stateLabel) stateLabel.classList.toggle('d-none', !isReposted);
                btn.title = isReposted ? 'Reposted' : 'Repost';
                btn.setAttribute('aria-label', isReposted ? 'Undo repost' : 'Repost');
            }
            const countSpan = btn.querySelector('.repost-count') || btn.querySelector('span.fw-bold') || btn.querySelector('span');
            if (countSpan && repostCount !== undefined && repostCount !== null) {
                countSpan.textContent = repostCount;
            }
        });

        // 6. Update Repost Badge Cluster in DOM immediately
        updateRepostBadgeDom(postId, isReposted, repostCount, userProfile);
    }

    function toggleReelRepost(postId) {
        if (!postId) return;
        postId = String(postId).trim();

        const snapItem = document.querySelector(`.reels-snap-item[data-post-id="${postId}"]`);
        const feedCard = document.getElementById(`reel-card-${postId}`) ||
                         document.getElementById(`post-card-${postId}`) ||
                         document.querySelector(`[data-post-id="${postId}"]`);
        const feedBtn = document.getElementById(`repost-btn-${postId}`);
        const fsRepostBtn = snapItem?.querySelector('.fs-repost-proxy-btn') || document.querySelector(`.fs-repost-proxy-btn[data-post-id="${postId}"]`);

        const isCurrentlyReposted = snapItem ? (snapItem.dataset.isReposted === 'true') :
                                    (feedCard && feedCard.dataset.isReposted !== undefined) ? (feedCard.dataset.isReposted === 'true') :
                                    (feedBtn?.classList.contains('text-primary') || fsRepostBtn?.classList.contains('is-reposted') || false);
        const willBeReposted = !isCurrentlyReposted;

        let currentCount = parseInt(
            snapItem?.dataset?.repostCount ||
            feedCard?.dataset?.repostCount ||
            document.getElementById(`fs-reel-repost-count-${postId}`)?.textContent ||
            feedBtn?.querySelector('.repost-count')?.textContent ||
            '0', 10
        ) || 0;
        let optimisticCount = willBeReposted ? currentCount + 1 : Math.max(0, currentCount - 1);

        syncRepostUiAcrossSite(postId, willBeReposted, optimisticCount);

        const csrfToken = getCsrfToken();
        fetch(`/api/posts/${postId}/repost/`, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken,
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({})
        })
        .then(res => res.json())
        .then(data => {
            if (data && data.status === 'success') {
                syncRepostUiAcrossSite(postId, data.is_reposted, data.repost_count, data);
            }
        })
        .catch(err => {
            console.warn('[VideoManager] Repost toggle error, reverting:', err);
            syncRepostUiAcrossSite(postId, isCurrentlyReposted, currentCount);
        });
    }

    function showSharedByNote(sharedBy) {
        if (!sharedBy) return;
        const noteEl = document.getElementById('fsReelSharedNote');
        const noteText = document.getElementById('fsReelSharedNoteText');
        if (noteEl && noteText) {
            const cleanUser = sharedBy.startsWith('@') ? sharedBy : `@${sharedBy}`;
            noteText.textContent = `${cleanUser} shared this reel with you`;
            noteEl.classList.remove('d-none');
            noteEl.style.opacity = '1';

            setTimeout(() => {
                noteEl.style.opacity = '0';
                setTimeout(() => {
                    noteEl.classList.add('d-none');
                }, 400);
            }, 5000);
        }
    }

    function checkAutoLaunchReel() {
        if (state.isFullScreenActive) return;

        let targetReelId = null;
        let sharedBy = null;

        const autoLaunchEl = document.getElementById('pwaniAutoLaunchReel');
        if (autoLaunchEl) {
            targetReelId = autoLaunchEl.dataset.reelId;
            sharedBy = autoLaunchEl.dataset.sharedBy;
            autoLaunchEl.remove();
        }

        if (!targetReelId) {
            try {
                const urlParams = new URLSearchParams(window.location.search);
                const reelParam = urlParams.get('reel');
                if (reelParam && reelParam !== '1' && reelParam !== 'true') {
                    targetReelId = reelParam;
                } else if (reelParam === '1' || reelParam === 'true') {
                    const pathMatch = window.location.pathname.match(/\/post\/([0-9a-f-]{36})/i);
                    if (pathMatch) {
                        targetReelId = pathMatch[1];
                    } else {
                        const detailEl = document.querySelector('.post-detail-container[data-post-id], .reel-post-card[data-post-id], .post-card[data-post-id]');
                        if (detailEl) targetReelId = detailEl.dataset.postId;
                    }
                }
                sharedBy = urlParams.get('shared_by');
            } catch (e) {}
        }

        if (targetReelId) {
            console.log('[VideoManager] Auto-launching fullscreen reel:', targetReelId, 'shared by:', sharedBy);
            setTimeout(() => {
                openFullscreenReels(targetReelId, sharedBy);
            }, 120);
        }
    }

    // ============================================================================
    // GLOBAL MUTE SYNCHRONIZATION
    // ============================================================================

    function getActiveFullscreenVideo() {
        if (!state.isFullScreenActive) return null;
        const activeSnap = getActiveSnapItem();
        return activeSnap ? activeSnap.querySelector('video') : null;
    }

    function syncMuteButtons(explicitMuted = null) {
        let isMuted = isGlobalMuted;
        if (explicitMuted !== null && explicitMuted !== undefined) {
            isMuted = !!explicitMuted;
        } else if (state.isFullScreenActive) {
            const activeFsVid = getActiveFullscreenVideo();
            if (activeFsVid) {
                isMuted = Boolean(activeFsVid.muted);
            }
        } else if (state.currentPlayingVideo) {
            isMuted = Boolean(state.currentPlayingVideo.muted);
        }

        const muteButtons = document.querySelectorAll('.reel-mute-btn, .video-mute-toggle, [data-action="mute-toggle"], #global-mute-button');
        muteButtons.forEach(btn => {
            const icon = btn.querySelector('i');
            if (icon) {
                if (isMuted) {
                    icon.className = icon.className.includes('bi-volume') ? 'bi bi-volume-mute-fill' : icon.className;
                    if (btn.id === 'global-mute-button') icon.className = 'bi bi-volume-mute';
                } else {
                    icon.className = icon.className.includes('bi-volume') ? 'bi bi-volume-up-fill' : icon.className;
                    if (btn.id === 'global-mute-button') icon.className = 'bi bi-volume-up';
                }
            }
            const label = btn.parentElement?.querySelector('.reel-mute-label, .reel-desktop-action-label.reel-mute-label');
            if (label) {
                label.textContent = isMuted ? 'Muted' : 'Sound';
            }
            btn.title = isMuted ? 'Unmute' : 'Mute';
            btn.setAttribute('aria-label', isMuted ? 'Unmute' : 'Mute');
        });
    }

    function setGlobalMute(muted) {
        isGlobalMuted = !!muted;
        try {
            localStorage.setItem(storageKey, isGlobalMuted ? 'muted' : 'unmuted');
            localStorage.setItem('vasto_audio_preference', isGlobalMuted ? 'muted' : 'unmuted');
        } catch (e) {
            console.warn('[VideoManager] LocalStorage unavailable for audio preference', e);
        }

        isProgrammaticAudioSync = true;
        try {
            // Sync all video elements across feed and overlays (excluding carousel preview videos and messaging videos)
            document.querySelectorAll('video').forEach(vid => {
                if (vid.classList.contains('reel-ambient-video')) {
                    // The blurred letterbox filler is a silent copy — always muted.
                    vid.muted = true;
                    return;
                }
                if (vid.classList.contains('reels-carousel-video') || vid.closest('.reels-carousel-shelf')) {
                    vid.muted = true;
                    return;
                }
                if (isMessagingVideo(vid)) {
                    return;
                }
                delete vid._isAutoplayMutedFallback;
                vid.muted = isGlobalMuted;
            });
        } finally {
            setTimeout(() => { isProgrammaticAudioSync = false; }, 80);
        }

        syncMuteButtons(isGlobalMuted);

        // Keep GlobalAudioManager preference in sync if present
        try {
            if (window.vastoGlobalAudio && typeof window.vastoGlobalAudio.getAudioPreference === 'function') {
                const targetPref = isGlobalMuted ? 'muted' : 'unmuted';
                if (window.vastoGlobalAudio.getAudioPreference() !== targetPref) {
                    if (typeof window.vastoGlobalAudio.setAudioPreference === 'function') {
                        window.vastoGlobalAudio.setAudioPreference(targetPref);
                    }
                }
            }
        } catch (_) {}

        console.log('[VideoManager] Global mute toggled to:', isGlobalMuted);
    }

    function toggleGlobalMute() {
        const activeVid = (state.isFullScreenActive ? getActiveFullscreenVideo() : state.currentPlayingVideo);
        const currentMuted = activeVid ? activeVid.muted : isGlobalMuted;
        setGlobalMute(!currentMuted);
    }

    // ============================================================================
    // AUTO-SCROLL CONTROLLER
    // ============================================================================

    function syncAutoScrollUI() {
        // Sync the quick tools modal toggle
        const toggleBtn = document.getElementById('quickToolsAutoScrollBtn');
        const statusBadge = document.getElementById('quickToolsAutoScrollStatus');
        if (toggleBtn) {
            toggleBtn.classList.toggle('active', isAutoScrollEnabled);
            const icon = toggleBtn.querySelector('i');
            if (icon) {
                icon.className = isAutoScrollEnabled ? 'bi bi-collection-play-fill text-primary' : 'bi bi-collection-play';
            }
        }
        if (statusBadge) {
            statusBadge.textContent = isAutoScrollEnabled ? 'On' : 'Off';
            statusBadge.className = isAutoScrollEnabled
                ? 'badge rounded-pill bg-primary text-white ms-auto auto-scroll-status-badge'
                : 'badge rounded-pill bg-secondary bg-opacity-25 text-muted ms-auto auto-scroll-status-badge';
        }
    }

    function setAutoScroll(enabled) {
        isAutoScrollEnabled = !!enabled;
        try {
            localStorage.setItem(autoScrollKey, isAutoScrollEnabled ? 'true' : 'false');
        } catch (e) {
            console.warn('[VideoManager] LocalStorage unavailable for auto-scroll preference', e);
        }
        syncAutoScrollUI();
        console.log('[VideoManager] Auto-scroll toggled to:', isAutoScrollEnabled);
    }

    function toggleAutoScroll() {
        setAutoScroll(!isAutoScrollEnabled);
    }

    function scrollToNextReel() {
        if (!state.isFullScreenActive) return;
        const viewport = document.getElementById('reelsSnapViewport');
        if (!viewport || viewport.children.length === 0) return;

        const h = viewport.clientHeight || 1;
        const curIdx = Math.round(viewport.scrollTop / h);
        const nextIdx = curIdx + 1;

        if (nextIdx >= viewport.children.length) {
            // At the last reel — try to load more
            const trigger = document.getElementById('feed-load-trigger');
            if (trigger && typeof htmx !== 'undefined') {
                htmx.trigger(trigger, 'revealed');
            }
            return;
        }

        viewport.scrollTo({ top: nextIdx * h, behavior: 'smooth' });
        updateFullscreenNavButtons(nextIdx);
    }

    // ============================================================================
    // AUTO-SCROLL PROMPT NUDGE
    // ============================================================================

    function _isPromptEligible() {
        if (isAutoScrollEnabled) return false;           // already on — never prompt
        if (_promptShownThisSession) return false;       // once per session max

        const dismissCount = parseInt(localStorage.getItem(promptCountKey) || '0', 10);
        if (dismissCount >= PROMPT_MAX_DISMISSALS) return false; // gave up after N dismissals

        const lastTs = parseInt(localStorage.getItem(promptTsKey) || '0', 10);
        if (lastTs && (Date.now() - lastTs) < PROMPT_SNOOZE_MS) return false; // in snooze window

        return true;
    }

    function _buildAutoScrollPrompt() {
        const el = document.createElement('div');
        el.id = 'autoScrollPromptBanner';
        el.setAttribute('role', 'dialog');
        el.setAttribute('aria-label', 'Auto-scroll suggestion');
        el.innerHTML = `
            <div class="as-prompt-icon">
                <i class="bi bi-collection-play-fill"></i>
            </div>
            <div class="as-prompt-body">
                <div class="as-prompt-title">Auto-scroll reels?</div>
                <div class="as-prompt-desc">Automatically advance to the next reel when one ends — no swiping needed.</div>
                <div class="as-prompt-actions">
                    <button type="button" class="as-prompt-btn-primary" id="asPromptAccept">
                        <i class="bi bi-check2"></i> Turn On
                    </button>
                    <button type="button" class="as-prompt-btn-ghost" id="asPromptDismiss">
                        Not now
                    </button>
                </div>
            </div>
            <button type="button" class="as-prompt-close" id="asPromptClose" aria-label="Dismiss">
                <i class="bi bi-x-lg"></i>
            </button>
        `;
        return el;
    }

    function _hideAutoScrollPrompt(animate = true) {
        const banner = document.getElementById('autoScrollPromptBanner');
        if (!banner) return;
        if (animate) {
            banner.classList.remove('as-prompt-visible');
            setTimeout(() => banner.remove(), 350);
        } else {
            banner.remove();
        }
    }

    function _showAutoScrollPrompt() {
        if (!state.isFullScreenActive) return;
        if (document.getElementById('autoScrollPromptBanner')) return; // already showing

        const stage = document.querySelector('.reel-desktop-stage');
        if (!stage) return;

        _promptShownThisSession = true;

        const banner = _buildAutoScrollPrompt();
        stage.appendChild(banner);

        // Trigger enter animation on next frame
        requestAnimationFrame(() => {
            requestAnimationFrame(() => banner.classList.add('as-prompt-visible'));
        });

        // Auto-hide after 12 seconds if user ignores it (counts as a soft dismiss)
        const autoHideTimer = setTimeout(() => {
            _dismissAutoScrollPrompt(false); // silent snooze, no count increment
        }, 12000);
        banner._autoHideTimer = autoHideTimer;

        // Wire buttons
        document.getElementById('asPromptAccept')?.addEventListener('click', (e) => {
            e.stopPropagation();
            clearTimeout(banner._autoHideTimer);
            _acceptAutoScrollPrompt();
        });

        document.getElementById('asPromptDismiss')?.addEventListener('click', (e) => {
            e.stopPropagation();
            clearTimeout(banner._autoHideTimer);
            _dismissAutoScrollPrompt(true);
        });

        document.getElementById('asPromptClose')?.addEventListener('click', (e) => {
            e.stopPropagation();
            clearTimeout(banner._autoHideTimer);
            _dismissAutoScrollPrompt(true);
        });
    }

    function _acceptAutoScrollPrompt() {
        _hideAutoScrollPrompt(true);
        setAutoScroll(true);
        // Clear any snooze state — they've accepted, we're done
        try {
            localStorage.removeItem(promptTsKey);
            localStorage.removeItem(promptCountKey);
        } catch (e) {}

        // Brief confirmation toast
        let toast = document.getElementById('autoScrollToast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'autoScrollToast';
            toast.className = 'position-fixed bottom-0 start-50 translate-middle-x mb-5 px-3 py-2 rounded-pill shadow text-white text-center';
            toast.style.cssText = 'z-index:10999;font-size:13px;pointer-events:none;transition:opacity 0.2s ease,transform 0.2s ease;';
            document.body.appendChild(toast);
        }
        toast.style.background = '#0d6efd';
        toast.textContent = '▶ Auto-scroll On';
        toast.style.opacity = '1';
        toast.style.transform = 'translate(-50%, 0)';
        clearTimeout(toast._t);
        toast._t = setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translate(-50%, 10px)';
        }, 2200);
    }

    function _dismissAutoScrollPrompt(incrementCount = true) {
        _hideAutoScrollPrompt(true);
        try {
            localStorage.setItem(promptTsKey, String(Date.now()));
            if (incrementCount) {
                const prev = parseInt(localStorage.getItem(promptCountKey) || '0', 10);
                localStorage.setItem(promptCountKey, String(prev + 1));
            }
        } catch (e) {}
    }

    function _tickPromptSessionCounter() {
        if (!state.isFullScreenActive) return;
        if (isAutoScrollEnabled) return;
        _promptSessionReelCount++;
        if (_promptSessionReelCount >= PROMPT_TRIGGER_REELS && _isPromptEligible()) {
            // Small delay so the reel is fully visible before the banner slides in
            setTimeout(_showAutoScrollPrompt, 1800);
        }
    }

    // ============================================================================
    // PLAYBACK CONTROLLER
    // ============================================================================

    function wireVideoBufferingListeners(video, container, spinnerEl, progressBarContainer, vinylEl) {
        if (!video || video._bufferingListenersAttached) return;
        video._bufferingListenersAttached = true;

        let bufferTimer = null;

        const setBuffering = (isBuffering) => {
            if (isBuffering) {
                // Only trigger buffering if actively playing and genuinely waiting for media data
                if (video.paused || video.ended || video.readyState >= 3) {
                    if (bufferTimer) {
                        clearTimeout(bufferTimer);
                        bufferTimer = null;
                    }
                    if (spinnerEl) spinnerEl.classList.add('d-none');
                    if (progressBarContainer) progressBarContainer.classList.remove('is-buffering');
                    return;
                }
                if (!bufferTimer) {
                    // Generous 750ms threshold: healthy connections never flash a spinner during brief fragment loads
                    bufferTimer = setTimeout(() => {
                        if (!video.paused && !video.ended && video.readyState < 3) {
                            if (spinnerEl) spinnerEl.classList.remove('d-none');
                            if (progressBarContainer) progressBarContainer.classList.add('is-buffering');
                            if (vinylEl) vinylEl.classList.remove('is-playing');
                        }
                    }, 750);
                }
            } else {
                if (bufferTimer) {
                    clearTimeout(bufferTimer);
                    bufferTimer = null;
                }
                if (spinnerEl) spinnerEl.classList.add('d-none');
                if (progressBarContainer) progressBarContainer.classList.remove('is-buffering');
                if (vinylEl && !video.paused && !video.ended) {
                    vinylEl.classList.add('is-playing');
                }
            }
        };

        // ONLY listen to waiting when actively trying to play (NEVER stalled, which is a normal socket idle event)
        video.addEventListener('waiting', () => {
            if (!video.paused && !video.ended && video.readyState < 3) {
                setBuffering(true);
            }
        });
        video.addEventListener('seeking', () => {
            if (!video.paused) {
                setBuffering(true);
            }
        });
        video.addEventListener('seeked', () => {
            setBuffering(false);
        });
        video.addEventListener('playing', () => {
            setBuffering(false);
            video.classList.add('is-playing');
            if (container) container.classList.add('is-video-playing');
            triggerPlayHud(video, true);
        });
        video.addEventListener('play', () => {
            video.classList.add('is-playing');
            if (container) container.classList.add('is-video-playing');
            triggerPlayHud(video, true);
        });
        video.addEventListener('canplay', () => setBuffering(false));
        video.addEventListener('canplaythrough', () => setBuffering(false));
        video.addEventListener('pause', () => {
            setBuffering(false);
            video.classList.remove('is-playing');
            if (container) container.classList.remove('is-video-playing');
            if (!video.seeking && !video.ended && video.dataset.wantsAutoplay !== 'true') {
                triggerPlayHud(video, false);
            }
        });
        video.addEventListener('ended', () => {
            setBuffering(false);
            video.classList.remove('is-playing');
            if (container) container.classList.remove('is-video-playing');
            triggerPlayHud(video, false);
        });
        video.addEventListener('timeupdate', () => {
            if (video._lastTimeUpdate !== video.currentTime) {
                video._lastTimeUpdate = video.currentTime;
                setBuffering(false);
            }
        });
    }

    function playVideo(video, isAutoplay = false) {
        if (!video) return;

        video.dataset.wantsAutoplay = 'true';

        // STRICT: Pause ALL other video elements across feed, fullscreen, and modals
        // (the blurred letterbox filler is excluded — it is driven by the reel
        //  it belongs to and must never own playback or trigger the reel HUD)
        document.querySelectorAll('video').forEach(otherVid => {
            if (otherVid !== video && !otherVid.paused && !otherVid.classList.contains('reel-ambient-video')) {
                try {
                    otherVid.pause();
                    delete otherVid.dataset.wantsAutoplay;
                    otherVid.classList.remove('is-playing');
                    triggerPlayHud(otherVid, false);
                    const otherContainer = otherVid.closest('.reel-card-container, .reel-post-card, .reel-stage-container, .reel-fullscreen-content, .reels-snap-item, .media-video-container, .landscape-video-container, .landscape-video-wrapper, .post-card, .post-media-wrapper');
                    if (otherContainer) {
                        otherContainer.classList.remove('is-video-playing');
                        triggerPlayHud(otherContainer, false);
                        const otherVinyl = otherContainer.querySelector('.reel-vinyl-disc');
                        if (otherVinyl) otherVinyl.classList.remove('is-playing');
                    }
                } catch (_) {}
            }
        });

        // Ensure HLS is attached before calling play() so attachMedia doesn't abort play()
        if (video.dataset.hlsUrl && !video.dataset.hlsReady && typeof window.initHLSForElement === 'function') {
            window.initHLSForElement(video);
        }

        // Apply global mute (carousel previews are always strictly muted)
        isProgrammaticAudioSync = true;
        if (video.classList.contains('reels-carousel-video') || video.closest('.reels-carousel-shelf')) {
            video.muted = true;
            video.volume = 0;
        } else {
            video.muted = isGlobalMuted;
        }
        setTimeout(() => { isProgrammaticAudioSync = false; }, 80);

        // Resume HLS buffer loading if active
        if (video._hlsInstance && typeof video._hlsInstance.startLoad === 'function') {
            video._hlsInstance.startLoad();
        }

        const container = video.closest('.reel-card-container, .reel-post-card, .reel-stage-container, .reel-fullscreen-content, .reels-snap-item, .media-video-container, .landscape-video-container, .landscape-video-wrapper, .post-card, .post-media-wrapper');
        const vinyl = container ? container.querySelector('.reel-vinyl-disc') : null;

        const onPlaySuccess = () => {
            state.currentPlayingVideo = video;
            video.classList.add('is-playing');
            if (video.classList.contains('reels-carousel-video')) {
                video.style.opacity = '1';
            }
            if (container) {
                container.classList.add('is-video-playing');
                triggerPlayHud(container, true);
            }
            triggerPlayHud(video, true);
            if (vinyl) {
                vinyl.classList.add('is-playing');
            }
            syncMuteButtons(video.muted);
            // If fullscreen is active, ensure orientation and desktop rail are updated for this playing video!
            if (state.isFullScreenActive) {
                const snapItem = video.closest('.reels-snap-item');
                if (snapItem) {
                    adaptSnapItemOrientation(snapItem);
                }
            }
        };

        const onPlayPausedOrBlocked = () => {
            if (video.paused) {
                video.classList.remove('is-playing');
                if (container) {
                    container.classList.remove('is-video-playing');
                    triggerPlayHud(container, false);
                }
                triggerPlayHud(video, false);
            }
            if (video.paused && vinyl) {
                vinyl.classList.remove('is-playing');
            }
        };

        // If media hasn't buffered enough frames yet, schedule autoplay as soon as canplay fires
        if (video.readyState < 2 && !video._canplayAutoplayBound) {
            video._canplayAutoplayBound = true;
            const retryOnReady = () => {
                video._canplayAutoplayBound = false;
                if (video.dataset.wantsAutoplay === 'true' && video.paused) {
                    playVideo(video, isAutoplay);
                }
            };
            video.addEventListener('canplay', retryOnReady, { once: true });
        }

        const playPromise = video.play();
        if (playPromise !== undefined) {
            playPromise.then(() => {
                onPlaySuccess();
            }).catch(err => {
                console.warn('[VideoManager] Play rejected:', err);
                if (err && err.name === 'AbortError') {
                    // Interrupted by media source load/HLS attachment; retry once ready
                    video.addEventListener('canplay', function retryAfterAbort() {
                        if (video.dataset.wantsAutoplay === 'true' && video.paused) {
                            playVideo(video, isAutoplay);
                        }
                    }, { once: true });
                    setTimeout(onPlayPausedOrBlocked, 280);
                    return;
                }

                if (isAutoplay && !video.muted) {
                    // Browser prevented unmuted autoplay: fallback to muted for this specific playback attempt only,
                    // without destroying the user's global audio preference!
                    isProgrammaticAudioSync = true;
                    video._isAutoplayMutedFallback = true;
                    video.muted = true;
                    syncMuteButtons(true);
                    video.play().then(() => {
                        onPlaySuccess();
                    }).catch(silentErr => {
                        console.log('[VideoManager] Muted retry also rejected:', silentErr);
                        onPlayPausedOrBlocked();
                    }).finally(() => {
                        setTimeout(() => { isProgrammaticAudioSync = false; }, 80);
                    });
                } else {
                    onPlayPausedOrBlocked();
                }
            });
        } else {
            if (!video.paused) {
                onPlaySuccess();
            } else {
                onPlayPausedOrBlocked();
            }
        }
    }

    function pauseVideo(video) {
        if (!video) return;

        delete video.dataset.wantsAutoplay;

        if (video._hlsInstance && typeof video._hlsInstance.stopLoad === 'function') {
            video._hlsInstance.stopLoad();
        }

        try {
            video.pause();
        } catch (e) {
            // Ignore pause errors
        }

        video.classList.remove('is-playing');
        const container = video.closest('.reel-card-container, .reel-post-card, .reel-stage-container, .reel-fullscreen-content, .reels-snap-item, .media-video-container, .landscape-video-container, .landscape-video-wrapper, .post-card, .post-media-wrapper');
        triggerPlayHud(video, false);
        if (container) {
            container.classList.remove('is-video-playing');
            triggerPlayHud(container, false);
            const vinyl = container.querySelector('.reel-vinyl-disc');
            if (vinyl) vinyl.classList.remove('is-playing');
        }

        if (state.currentPlayingVideo === video) {
            state.currentPlayingVideo = null;
        }
    }

    function pauseAllVideos() {
        stopCarouselAutoplay();
        document.querySelectorAll('video').forEach(vid => {
            pauseVideo(vid);
        });
    }

    function cleanupVideo(video) {
        if (!video) return;

        try {
            video.pause();
        } catch (e) {}

        if (state.feedObserver) {
            try {
                state.feedObserver.unobserve(video);
            } catch (e) {}
        }
        if (state.fullscreenObserver) {
            try {
                state.fullscreenObserver.unobserve(video);
            } catch (e) {}
        }

        if (video._hlsInstance && typeof video._hlsInstance.destroy === 'function') {
            try {
                video._hlsInstance.destroy();
            } catch (e) {}
            video._hlsInstance = null;
        }

        if (state.currentPlayingVideo === video) {
            state.currentPlayingVideo = null;
        }

        state.videos.delete(video);
        delete video.dataset.pwaniObserved;

        // Free decoder memory on unmount/teardown
        try {
            video.removeAttribute('src');
            while (video.firstChild) {
                video.removeChild(video.firstChild);
            }
            video.load();
        } catch (e) {}
    }

    // ============================================================================
    // INTERSECTION OBSERVERS (FEED & FULLSCREEN)
    // ============================================================================

    function isMessagingVideo(video) {
        if (!video) return false;
        if (video.dataset && (
            video.dataset.chatMedia === 'true' ||
            video.dataset.noInline === 'true' ||
            video.dataset.messaging === 'true' ||
            (video.dataset.autoplay === 'false' && video.closest('.media-bubble, .media-tile, #messagesContainer, .messages-area'))
        )) {
            return true;
        }
        if (video.closest('#messagesContainer, .messages-area, .media-bubble, .media-tile, .message-bubble, .message-wrapper, .conversation-container, #chatMessages, #chatAppContainer, .messaging-view, #mediaPreviewModal, .media-preview-modal, .media-composer-modal, .media-viewer-overlay')) {
            return true;
        }
        return false;
    }

    function handleFeedIntersection(entries) {
        if (state.isFullScreenActive) return; // Fullscreen overlay has priority

        // 1. Update intersection ratio cache on observed videos
        entries.forEach(entry => {
            const video = entry.target;
            if (isMessagingVideo(video)) {
                if (state.feedObserver) {
                    try { state.feedObserver.unobserve(video); } catch (_) {}
                }
                video._feedIntersectionRatio = 0;
                try { video.pause(); } catch (_) {}
                return;
            }
            video._feedIntersectionRatio = entry.isIntersecting ? entry.intersectionRatio : 0;
            // When video leaves the viewport substantially, reset manual pause intent
            if (!entry.isIntersecting || entry.intersectionRatio < 0.2) {
                delete video.dataset.userPaused;
            }
        });

        // 2. Evaluate currently playing video
        const currentActive = state.currentPlayingVideo;
        if (currentActive && isMessagingVideo(currentActive)) {
            try { currentActive.pause(); } catch (_) {}
            state.currentPlayingVideo = null;
        }
        const currentActiveRatio = (currentActive && currentActive._feedIntersectionRatio !== undefined)
            ? currentActive._feedIntersectionRatio
            : 0;

        // If currently playing video has dropped below 35% visible, pause it
        if (currentActive && currentActiveRatio < 0.35) {
            pauseVideo(currentActive);
        }

        // 3. Find candidate video with highest visibility across the feed
        let bestCandidate = null;
        let bestRatio = 0;

        document.querySelectorAll('video').forEach(video => {
            if (isMessagingVideo(video)) {
                return;
            }
            if (video.classList.contains('reels-carousel-video') ||
                video.closest('.reels-carousel-shelf') ||
                video.closest('#fullscreenReelsOverlay') ||
                video.closest('#reelsSnapViewport')) {
                return;
            }
            if (video.dataset.autoplay === 'false' || video.dataset.userPaused === 'true') {
                return;
            }

            const ratio = video._feedIntersectionRatio || 0;
            if (ratio > bestRatio) {
                bestRatio = ratio;
                bestCandidate = video;
            }
        });

        // 4. Hysteresis decision:
        // If current video is still healthy (>= 35% visible), only switch if the incoming video is dominant (>= 55% and higher than current)
        // If no video is currently playing, start the best candidate if it reaches >= 45% visibility
        if (state.currentPlayingVideo && !state.currentPlayingVideo.paused && state.currentPlayingVideo.dataset.userPaused !== 'true') {
            if (bestCandidate && bestCandidate !== state.currentPlayingVideo && bestRatio >= 0.55 && bestRatio > currentActiveRatio + 0.15) {
                playVideo(bestCandidate, true);
            }
        } else {
            if (bestCandidate && bestRatio >= 0.45) {
                playVideo(bestCandidate, true);
            }
        }
    }

    // ============================================================================
    // VIRTUALIZED THREE-SLOT PLAYBACK ENGINE & LIFECYCLE (PHASE 1)
    // ============================================================================

    /**
     * Generic, robust media eviction for a snap item.
     * Guaranteed to release both HLS instances and native HTML5 / MP4 MediaCodec decoders.
     */
    function evictSnapItemMedia(snapItem) {
        if (!snapItem) return;
        const video = snapItem.querySelector('video');
        if (!video) return;

        // Free the blurred-fill copy along with the reel itself.
        detachAmbientFill(snapItem);

        const hasActiveMedia = video._hlsInstance || video.currentSrc || video.getAttribute('src') || video.querySelector('source') || video.dataset.isWarmed === 'true';
        if (!hasActiveMedia) return;

        // 1. Pause and reset playback intent
        try { video.pause(); } catch (_) {}
        delete video.dataset.wantsAutoplay;
        delete video.dataset.isWarmed;
        video.classList.remove('is-playing');

        // 2. Destroy HLS instance & clear references
        if (video._hlsInstance && typeof video._hlsInstance.destroy === 'function') {
            try { video._hlsInstance.destroy(); } catch (_) {}
            video._hlsInstance = null;
        }
        video.dataset.hlsReady = '';

        // 3. For MP4 / HTML5 direct media: remove src and all source elements, then call load()
        // Calling load() after removing src is the W3C-specified method to force Chromium / WebKit
        // and Android MediaCodec to terminate the hardware decoding context.
        try {
            video.removeAttribute('src');
            try { video.src = ''; } catch (_) {}
            while (video.firstChild) {
                video.removeChild(video.firstChild);
            }
            video.load();
        } catch (_) {}

        // 4. Reset indicators
        const spinner = snapItem.querySelector('.reel-buffering-indicator');
        if (spinner) spinner.classList.add('d-none');
        const progress = snapItem.querySelector('.reel-progress-container');
        if (progress) progress.classList.remove('is-buffering');
        const vinyl = snapItem.querySelector('.reel-vinyl-disc');
        if (vinyl) vinyl.classList.remove('is-playing');
    }

    /**
     * Warm media pipeline for a slot in [N-1, N+1].
     * Network-aware: avoids speculative buffering on slow/save-data connections to protect active video N.
     */
    function warmSnapItemMedia(snapItem, isNext = false) {
        if (!snapItem) return;
        const video = snapItem.querySelector('video');
        if (!video) return;

        if (video.dataset.isWarmed === 'true') {
            return; // Already warmed
        }

        const conn = typeof navigator !== 'undefined' ? (navigator.connection || navigator.mozConnection || navigator.webkitConnection) : null;
        const isSaveData = Boolean(conn && (conn.saveData || conn.effectiveType === '2g' || conn.effectiveType === 'slow-2g'));

        // On slow connections or save-data mode, avoid speculative pre-buffering to prioritize active video N
        if (isSaveData && isNext) {
            video.preload = 'none';
            return;
        }

        video.dataset.isWarmed = 'true';
        video.preload = 'auto';

        const hlsUrl = video.dataset.hlsUrl;
        const videoUrl = video.dataset.videoUrl;

        if (hlsUrl) {
            if (typeof window.initHLSForElement === 'function') {
                delete video.dataset.wantsAutoplay;
                window.initHLSForElement(video);
            }
        } else if (videoUrl) {
            if (!video.src && !video.querySelector('source')) {
                video.src = videoUrl;
                const srcEl = document.createElement('source');
                srcEl.src = videoUrl;
                srcEl.type = 'video/mp4';
                video.appendChild(srcEl);
                video.load();
            }
        }
    }

    /**
     * Authoritative three-slot window updater.
     * Manages [N-1, N, N+1] and evicts all items with |index - activeIndex| >= 2.
     * Guarantees only the active reel plays.
     */
    function updateThreeSlotWindow(newActiveIndex, originCurrentTime = 0) {
        if (!state.isFullScreenActive) return;
        const viewport = document.getElementById('reelsSnapViewport');
        if (!viewport) return;
        const items = Array.from(viewport.children);
        if (items.length === 0) return;

        newActiveIndex = Math.max(0, Math.min(items.length - 1, newActiveIndex));
        reelSlotEngine.activeIndex = newActiveIndex;
        reelSlotEngine.previousIndex = newActiveIndex > 0 ? newActiveIndex - 1 : -1;
        reelSlotEngine.nextIndex = newActiveIndex < items.length - 1 ? newActiveIndex + 1 : -1;

        // 1. Evict any snap item with |distance| >= 2 (frees decoders and RAM)
        items.forEach((item, idx) => {
            const distance = Math.abs(idx - newActiveIndex);
            if (distance >= 2) {
                evictSnapItemMedia(item);
                item.classList.remove('is-active-reel', 'is-warmed-reel');
            }
        });

        // 1b. Release the blurred-fill copy of every reel that is no longer the
        //     active one — only the active reel keeps a second decoder alive.
        items.forEach((item, idx) => {
            if (idx !== newActiveIndex) {
                detachAmbientFill(item);
            }
        });

        // 2. Warm N-1 (previous) if exists - kept strictly paused
        if (reelSlotEngine.previousIndex !== -1) {
            const prevItem = items[reelSlotEngine.previousIndex];
            warmSnapItemMedia(prevItem, false);
            prevItem.classList.remove('is-active-reel');
            prevItem.classList.add('is-warmed-reel');
            const prevVid = prevItem.querySelector('video');
            if (prevVid) {
                delete prevVid.dataset.wantsAutoplay;
                if (!prevVid.paused) {
                    try { prevVid.pause(); } catch (_) {}
                }
            }
        }

        // 3. Warm N+1 (next) if exists - kept strictly paused
        if (reelSlotEngine.nextIndex !== -1) {
            const nextItem = items[reelSlotEngine.nextIndex];
            warmSnapItemMedia(nextItem, true);
            nextItem.classList.remove('is-active-reel');
            nextItem.classList.add('is-warmed-reel');
            const nextVid = nextItem.querySelector('video');
            if (nextVid) {
                delete nextVid.dataset.wantsAutoplay;
                if (!nextVid.paused) {
                    try { nextVid.pause(); } catch (_) {}
                }
            }
        }

        // 4. Ensure N (active) is warmed and played
        const activeItem = items[newActiveIndex];
        if (activeItem) {
            activeItem.classList.remove('is-warmed-reel');
            activeItem.classList.add('is-active-reel');
            warmSnapItemMedia(activeItem, false);

            const activeVid = activeItem.querySelector('video');
            if (activeVid) {
                isProgrammaticAudioSync = true;
                activeVid.muted = isGlobalMuted;
                setTimeout(() => { isProgrammaticAudioSync = false; }, 80);
                if (originCurrentTime > 0) {
                    const applyOriginTime = () => {
                        try { activeVid.currentTime = originCurrentTime; } catch (_) {}
                    };
                    if (activeVid.readyState >= 1) {
                        applyOriginTime();
                    } else {
                        activeVid.addEventListener('loadedmetadata', applyOriginTime, { once: true });
                    }
                }
                playVideo(activeVid, true);
                // Blurred fill for the letterbox band (mobile fullscreen only)
                attachAmbientFill(activeItem);
                syncMuteButtons(isGlobalMuted);
                const vinyl = activeItem.querySelector('.reel-vinyl-disc');
                if (vinyl) vinyl.classList.add('is-playing');
            }
            adaptSnapItemOrientation(activeItem);
            updateDesktopSideRail(activeItem);
            updateFullscreenNavButtons(newActiveIndex);

            // Soft 200ms crossfade on every reel hand-off so the video swap
            // never flashes black (slow CDN/HLS loads are hidden behind it).
            if (!activeItem._reelEnterWired) {
                activeItem._reelEnterWired = true;
                activeItem.addEventListener('animationend', (e) => {
                    if (e.animationName === 'reelCardEnter') activeItem.classList.remove('is-entering');
                });
            }
            activeItem.classList.remove('is-entering');
            void activeItem.offsetWidth; // force reflow so the animation restarts
            activeItem.classList.add('is-entering');

            // Warm the NEXT reel's comments while this one plays — by the time
            // the user swipes, the rail is already painted (no skeleton flash).
            if (reelSlotEngine.nextIndex !== -1) {
                prefetchReelComments(extractPostId(items[reelSlotEngine.nextIndex]));
            }
        }

        // Check if approaching end of queue to load more cards
        if (activeItem) {
            checkAndAppendMoreFullscreenVideos(activeItem);
        }
    }

    /**
     * Suspend background feed videos when fullscreen reels is opened.
     * Prevents feed decoders and HLS workers from competing with fullscreen playback.
     */
    function suspendFeedVideos() {
        reelSlotEngine.suspendedFeedVideos = [];
        if (state.feedObserver) {
            try { state.feedObserver.disconnect(); } catch (_) {}
        }
        document.querySelectorAll('video').forEach(vid => {
            if (isMessagingVideo(vid) || vid.closest('#fullscreenReelsOverlay') || vid.closest('#reelsSnapViewport')) {
                return;
            }
            try { vid.pause(); } catch (_) {}
            delete vid.dataset.wantsAutoplay;
            vid.classList.remove('is-playing');
            if (vid._hlsInstance && typeof vid._hlsInstance.stopLoad === 'function') {
                try { vid._hlsInstance.stopLoad(); } catch (_) {}
            }
            reelSlotEngine.suspendedFeedVideos.push(vid);
        });
    }

    /**
     * Restore background feed videos when fullscreen reels is closed.
     */
    function restoreFeedVideos() {
        if (Array.isArray(reelSlotEngine.suspendedFeedVideos)) {
            reelSlotEngine.suspendedFeedVideos.forEach(vid => {
                if (vid && vid._hlsInstance && typeof vid._hlsInstance.startLoad === 'function') {
                    try { vid._hlsInstance.startLoad(); } catch (_) {}
                }
            });
        }
        reelSlotEngine.suspendedFeedVideos = [];
        if (!state.isFullScreenActive) {
            createFeedObserver();
            document.querySelectorAll('video').forEach(vid => {
                if (isMessagingVideo(vid) || vid.closest('#fullscreenReelsOverlay') || vid.closest('#reelsSnapViewport')) {
                    return;
                }
                if (state.feedObserver && !vid.classList.contains('reels-carousel-video') && !vid.closest('.reels-carousel-shelf')) {
                    try { state.feedObserver.observe(vid); } catch (_) {}
                }
            });
        }
    }

    function handleFullscreenIntersection(entries) {
        if (!state.isFullScreenActive) return;

        entries.forEach(entry => {
            const snapItem = entry.target;
            const video = snapItem.querySelector('video');
            if (!video) return;

            if (entry.isIntersecting && entry.intersectionRatio >= 0.65) {
                const viewport = document.getElementById('reelsSnapViewport');
                if (viewport) {
                    const items = Array.from(viewport.children);
                    const snapIdx = items.indexOf(snapItem);
                    if (snapIdx !== -1 && snapIdx !== reelSlotEngine.activeIndex) {
                        updateThreeSlotWindow(snapIdx);
                        _tickPromptSessionCounter();
                    }
                }
            } else if (!entry.isIntersecting || entry.intersectionRatio < 0.2) {
                // If snap item leaves viewport and is >= 2 positions away from active, evict it
                const viewport = document.getElementById('reelsSnapViewport');
                if (viewport) {
                    const items = Array.from(viewport.children);
                    const snapIdx = items.indexOf(snapItem);
                    if (snapIdx !== -1 && reelSlotEngine.activeIndex !== -1) {
                        if (Math.abs(snapIdx - reelSlotEngine.activeIndex) >= 2) {
                            evictSnapItemMedia(snapItem);
                        }
                    }
                }
            }
        });
    }

    function createFeedObserver() {
        if (state.feedObserver) {
            state.feedObserver.disconnect();
        }
        state.feedObserver = new IntersectionObserver(handleFeedIntersection, {
            root: null,
            rootMargin: '0px',
            threshold: [0.15, 0.35, 0.5, 0.65, 0.8]
        });
    }

    function createFullscreenObserver() {
        if (state.fullscreenObserver) {
            state.fullscreenObserver.disconnect();
        }
        state.fullscreenObserver = new IntersectionObserver(handleFullscreenIntersection, {
            root: document.getElementById('reelsSnapViewport') || null,
            rootMargin: '0px',
            threshold: 0.65
        });
    }

    function appendNewVideosToFullscreenViewport() {
        if (!state.isFullScreenActive) return;
        const viewport = document.getElementById('reelsSnapViewport');
        if (!viewport) return;

        const existingIds = new Set(Array.from(viewport.children).map(item => String(item.dataset.postId).trim()));

        const cardSelectors = [
            '.reel-card-container',
            '.reel-post-card',
            '.reels-carousel-card',
            '.post-card',
            '.landscape-video-container'
        ];

        document.querySelectorAll(cardSelectors.join(', ')).forEach(card => {
            const vid = card.querySelector('video') || (card.tagName === 'VIDEO' ? card : null);
            if (!vid || card.classList.contains('reels-carousel-shelf')) return;

            const cardPostId = String(extractPostId(card) || vid.dataset.postId || card.dataset.postId || '').trim();
            if (!cardPostId || cardPostId === 'null' || cardPostId === 'undefined' || existingIds.has(cardPostId)) return;

            existingIds.add(cardPostId);
            const snapItem = buildSnapItemFromReelCard(card);
            if (snapItem) {
                viewport.appendChild(snapItem);
                if (window.htmx) {
                    window.htmx.process(snapItem);
                }
                if (state.fullscreenObserver) {
                    state.fullscreenObserver.observe(snapItem);
                }
            }
        });

        updateFullscreenNavButtons();
        const activeSnap = getActiveSnapItem();
        if (activeSnap) {
            adaptSnapItemOrientation(activeSnap);
        }
    }

    function checkAndAppendMoreFullscreenVideos(currentSnapItem) {
        if (!state.isFullScreenActive) return;
        const viewport = document.getElementById('reelsSnapViewport');
        if (!viewport) return;

        const items = Array.from(viewport.children);
        const idx = items.indexOf(currentSnapItem);

        if (idx >= items.length - 2) {
            appendNewVideosToFullscreenViewport();

            const trigger = document.getElementById('feed-load-trigger');
            if (trigger && typeof htmx !== 'undefined') {
                htmx.trigger(trigger, 'revealed');
            }
        }
    }

    // ============================================================================
    // HITBOX INTERACTIONS (SINGLE TAP HUD VS. DOUBLE TAP HEART BURST)
    // ============================================================================

    function triggerHeartBurst(container, clientX, clientY) {
        if (!container) return;
        const heart = container.querySelector('.reel-heart-burst');
        if (!heart) return;

        heart.classList.remove('animate');
        // Force reflow
        void heart.offsetWidth;
        heart.classList.add('animate');

        setTimeout(() => {
            heart.classList.remove('animate');
        }, 650);
    }

    function triggerPlayHud(target, isPlaying) {
        if (!target) return;
        const huds = new Set();
        if (target.classList && target.classList.contains('reel-play-hud')) {
            huds.add(target);
        }
        const postId = target.dataset ? (target.dataset.postId || extractPostId(target)) : null;
        if (postId) {
            const byId1 = document.getElementById(`video-play-hud-${postId}`);
            const byId2 = document.getElementById(`reel-play-hud-${postId}`);
            if (byId1) huds.add(byId1);
            if (byId2) huds.add(byId2);
        }
        if (target.parentElement) {
            target.parentElement.querySelectorAll('.reel-play-hud').forEach(h => huds.add(h));
        }
        if (target.querySelectorAll) {
            target.querySelectorAll('.reel-play-hud').forEach(h => huds.add(h));
        }
        const c = target.closest ? target.closest('.reel-card-container, .reel-post-card, .reel-stage-container, .reel-fullscreen-content, .reels-snap-item, .media-video-container, .landscape-video-container, .landscape-video-wrapper, .post-card, .post-media-wrapper') : null;
        if (c) {
            c.querySelectorAll('.reel-play-hud').forEach(h => huds.add(h));
        }

        huds.forEach(hud => {
            if (hud._hideTimer) {
                clearTimeout(hud._hideTimer);
                hud._hideTimer = null;
            }

            const icon = hud.querySelector('i');
            if (icon) {
                icon.className = 'bi bi-play-fill';
            }

            if (!isPlaying) {
                // Persistently show Play button indicator while video is paused
                hud.classList.remove('show');
                void hud.offsetWidth;
                hud.classList.add('show', 'is-paused');
            } else {
                // Hide Play button indicator when video is playing
                hud.classList.remove('is-paused', 'show');
            }
        });
    }

    function handleHitboxTap(hitbox, event) {
        if (state.hasLongPressed) {
            state.hasLongPressed = false;
            return;
        }

        // Swipes and drags must never trigger play/pause or double-tap likes
        if (isGestureDrag) {
            return;
        }

        const container = (hitbox.closest && hitbox.closest('.reel-card-container, .reel-post-card, .reel-stage-container, .reel-fullscreen-content, .reels-snap-item, .media-video-container, .landscape-video-container, .landscape-video-wrapper, .post-card, .post-media-wrapper')) || hitbox;
        if (!container) return;

        const video = container.querySelector ? container.querySelector('video') : (container.tagName === 'VIDEO' ? container : null);
        if (!video) return;

        const postId = extractPostId(container);

        // Double tap: heart burst animation + like (works in both feed and fullscreen)
        if (state.tapTimers.has(hitbox)) {
            clearTimeout(state.tapTimers.get(hitbox));
            state.tapTimers.delete(hitbox);

            if (!window.isAuthenticated) {
                if (typeof window.showGuestAuthPrompt === 'function') {
                    window.showGuestAuthPrompt({
                        title: 'Sign in to like',
                        subtitle: 'Create an account or sign in to like this reel and support the creator.',
                        icon: 'bi-heart-fill'
                    });
                }
                return;
            }

            const clickX = event?.clientX || (event?.touches && event.touches[0]?.clientX);
            const clickY = event?.clientY || (event?.touches && event.touches[0]?.clientY);
            triggerHeartBurst(container, clickX, clickY);

            if (postId) {
                const snapItem = container.closest ? container.closest('.reels-snap-item') : null;
                const isCurrentlyLiked = snapItem ? (snapItem.dataset.isLiked === 'true') :
                    Boolean(container.querySelector && (container.querySelector('.fs-like-proxy-btn.liked') || container.querySelector('.like-button.liked') || container.querySelector('.like-button.text-danger')));
                if (!isCurrentlyLiked) {
                    toggleReelLike(postId);
                }
            }
            return;
        }

        // Single tap: toggle play / pause (in both feed and fullscreen)
        const timer = setTimeout(() => {
            state.tapTimers.delete(hitbox);

            if (video.paused) {
                delete video.dataset.userPaused;
                playVideo(video, false);
                triggerPlayHud(container, true);
            } else {
                video.dataset.userPaused = 'true';
                pauseVideo(video);
                triggerPlayHud(container, false);
            }
        }, 180);

        state.tapTimers.set(hitbox, timer);
    }

    // ============================================================================
    // PROGRESSIVE FALLBACK FOR LEGACY PORTRAIT VIDEOS
    // ============================================================================

    function checkAndPromoteLegacyVideo(video) {
        if (!video) return;

        const inspectAndApply = () => {
            if (!video.videoWidth || !video.videoHeight) return;
            const isPortrait = video.videoHeight > video.videoWidth;
            if (!isPortrait) return;

            const landscapeContainer = video.closest('.landscape-video-container');
            if (landscapeContainer && !landscapeContainer.classList.contains('is-promoted-reel')) {
                landscapeContainer.classList.remove('ratio-16x9');
                landscapeContainer.classList.add('ratio-9x16', 'is-promoted-reel');
                const wrapper = landscapeContainer.closest('.landscape-video-wrapper');
                if (wrapper) {
                    wrapper.classList.add('is-promoted-reel-wrapper');
                }
                console.log('[VideoManager] Promoted legacy vertical video to reel aspect ratio:', video.id);
            }
        };

        if (video.readyState >= 1) {
            inspectAndApply();
        } else {
            video.addEventListener('loadedmetadata', inspectAndApply, { once: true });
        }
    }

    // ============================================================================
    // FULL-SCREEN REELS OVERLAY MODE
    // ============================================================================

    function buildSnapItemFromReelCard(card) {
        const rawPostId = card.dataset.postId || extractPostId(card);
        const postId = rawPostId ? String(rawPostId).trim() : '';
        if (!postId || postId === 'null' || postId === 'undefined') return null;

        const video = card.querySelector('video');
        if (!video) return null;

        const videoSrc = video.dataset.videoUrl || (video.querySelector('source') ? video.querySelector('source').getAttribute('src') : '') || video.currentSrc || video.getAttribute('src') || '';
        const poster = video.getAttribute('poster') ||
                       card.querySelector('.reel-ambient-img')?.getAttribute('src') ||
                       card.querySelector('.media-download-btn')?.dataset?.thumbnail ||
                       '';
        const hlsUrl = video.dataset.hlsUrl || '';

        // Extract author avatar & details (supports datasets from carousel, reel cards, and standard postcards)
        const authorImg = card.querySelector('.fb-author-avatar-img') ||
                          card.querySelector('.reel-creator-row img') ||
                          card.querySelector('.fb-avatar-container img') ||
                          card.querySelector('.reel-scrim-bottom img') ||
                          card.querySelector('.post-avatar-img') ||
                          card.querySelector('.reel-vinyl-art');
        const authorAvatar = card.dataset.authorAvatar || authorImg?.getAttribute('src') || '/static/images/default-avatar.png';

        const authorLink = card.querySelector('.fb-author-name') ||
                           card.querySelector('.reel-creator-row a') ||
                           card.querySelector('.reel-scrim-bottom a[href*="profile"]') ||
                           card.querySelector('a[href*="/users/user/"]') ||
                           card.querySelector('a[href*="/users/profile/"]');
        const authorName = card.dataset.author || authorLink?.textContent?.trim() || card.querySelector('.reel-options-btn')?.dataset.author || 'Author';
        const profileUrlHref = authorLink?.getAttribute('href') || '';
        const hrefMatch = profileUrlHref.match(/\/users\/user\/([^\/]+)/) || profileUrlHref.match(/\/users\/profile\/([^\/]+)/);
        const authorUsername = card.dataset.authorUsername ||
                               (hrefMatch ? hrefMatch[1] : null) ||
                               card.querySelector('.fb-author-username')?.textContent?.replace(/^@/, '')?.trim() ||
                               (authorName.startsWith('@') ? authorName.slice(1) : authorName);
        const profileUrl = card.dataset.authorProfile ||
                           profileUrlHref ||
                           `/users/user/${authorUsername}/`;

        // Extract campus unit badge and time if present
        const unitBadge = card.querySelector('.reel-unit-badge') || card.querySelector('.fb-sub-meta .badge');
        const unitCode = card.dataset.unit || unitBadge?.textContent?.trim() || '';
        const unitHtml = unitCode ?
            `<span class="badge bg-white bg-opacity-25 text-white border border-white border-opacity-20 px-2 py-1 reel-unit-badge"><i class="bi bi-mortarboard-fill me-1"></i>${unitCode}</span>` : '';

        const timeAgo = card.dataset.time ||
                        card.querySelector('.fb-sub-meta span:not(.badge)')?.textContent?.trim() ||
                        card.querySelector('.reel-scrim-top .text-white-50')?.textContent?.trim() ||
                        '';

        // Extract or construct follow chip
        const isFollowing = card.dataset.isFollowing === 'true';
        const isSelf = card.dataset.isSelf === 'true';
        let followBtnHtml = '';
        if (!isSelf && !isFollowing && authorUsername && authorUsername !== 'Author') {
            const followUrl = card.dataset.followUrl || `/users/follow/${authorUsername}/`;
            followBtnHtml = `
                <button type="button"
                        class="reel-follow-chip"
                        data-follow-url="${followUrl}"
                        data-author-username="${authorUsername}"
                        title="Follow"
                        aria-label="Follow ${authorUsername}">
                    Follow
                </button>
            `;
        }

        // Extract or construct creator row (Avatar + Name + Handle + Follow chip + Time)
        const creatorRow = card.querySelector('.reel-creator-row');
        let creatorRowHtml = '';
        if (creatorRow) {
            creatorRowHtml = creatorRow.outerHTML;
        } else {
            creatorRowHtml = `
                <div class="reel-creator-row d-flex align-items-center gap-2 mb-2">
                    <div class="position-relative flex-shrink-0">
                        <a href="${profileUrl}"
                           hx-get="${profileUrl}"
                           hx-target="#page-content-target"
                           hx-swap="innerHTML"
                           hx-push-url="true"
                           class="d-block text-decoration-none">
                            <img src="${authorAvatar}"
                                 class="rounded-circle border border-2 border-white shadow-sm"
                                 style="width: 40px; height: 40px; object-fit: cover;"
                                 alt="${authorUsername}">
                        </a>
                    </div>
                    <div class="min-w-0">
                        <div class="d-flex align-items-center gap-2">
                            <a href="${profileUrl}"
                               hx-get="${profileUrl}"
                               hx-target="#page-content-target"
                               hx-swap="innerHTML"
                               hx-push-url="true"
                               class="text-white fw-bold text-decoration-none text-truncate d-inline-block mw-100"
                               style="font-size: 0.92rem; text-shadow: 0 1px 3px rgba(0,0,0,0.9);">
                                @${authorUsername}
                            </a>
                            ${followBtnHtml}
                        </div>
                        <div class="text-white-50 small text-truncate d-flex align-items-center gap-1" style="font-size: 11px; text-shadow: 0 1px 2px rgba(0,0,0,0.85); line-height: 1.2;">
                            <span>${timeAgo}</span>
                            <span>&middot;</span>
                            <i class="bi bi-globe-americas" style="font-size: 10px;" title="Public"></i>
                        </div>
                    </div>
                </div>
            `;
        }

        // Extract caption with expander
        const captionWrap = card.querySelector('.reel-caption-wrap');
        let captionHtml = '';
        if (captionWrap) {
            captionHtml = captionWrap.outerHTML;
        } else {
            const rawCaption = card.dataset.caption ||
                               card.querySelector('.reel-caption-text')?.innerHTML ||
                               card.querySelector('.post-content-text')?.innerHTML ||
                               card.querySelector('.post-text-body')?.innerHTML ||
                               '';
            const cleanText = rawCaption.trim();
            if (cleanText) {
                const needsMore = cleanText.length > 70;
                captionHtml = `
                    <div class="reel-caption-wrap mb-2">
                        <div id="fs-reel-caption-${postId}" class="reel-caption-text post-text-clamp-2">
                            ${cleanText}
                        </div>
                        ${needsMore ? `
                        <button type="button"
                                class="reel-caption-toggle"
                                onclick="event.stopPropagation(); (function(btn){
                                    const c = btn.parentElement.querySelector('.reel-caption-text');
                                    if (!c) return;
                                    const exp = c.classList.toggle('is-expanded');
                                    btn.textContent = exp ? 'less' : '... more';
                                })(this);">
                            ... more
                        </button>` : ''}
                    </div>
                `;
            }
        }

        // Extract like count & state
        const likeBtn = card.querySelector('.like-button');
        const likeCount = card.dataset.likes ||
                          card.querySelector(`[id^="reel-like-count-"]`)?.textContent?.trim() ||
                          card.querySelector('.postcard-like-count')?.textContent?.trim() ||
                          card.querySelector('.like-count')?.textContent?.trim() ||
                          card.querySelector(`[id^="like-section-"] .fw-bold`)?.textContent?.trim() ||
                          '0';
        const isLiked = card.dataset.isLiked === 'true' ||
                        likeBtn?.classList?.contains('liked') ||
                        likeBtn?.classList?.contains('text-danger') ||
                        false;

        // Extract comment count
        const commentCount = card.dataset.comments ||
                             card.querySelector(`[id^="reel-comment-count-"]`)?.textContent?.trim() ||
                             card.querySelector(`[id^="comment-count-"]`)?.textContent?.trim() ||
                             card.querySelector('.postcard-comment-btn span')?.textContent?.trim() ||
                             '0';

        // Extract repost count & state
        const repostBtn = card.querySelector('.repost-button');
        const repostCount = card.dataset.repostCount ||
                            card.querySelector(`[id^="repost-count-"]`)?.textContent?.trim() ||
                            card.querySelector('.repost-count')?.textContent?.trim() ||
                            '0';
        const isReposted = card.dataset.isReposted === 'true' ||
                           repostBtn?.classList?.contains('text-primary') ||
                           false;

        // Extract or construct scattered repost badge
        let repostBadgeHtml = '';
        if (card.dataset.repostBadge) {
            try {
                const badgeData = typeof card.dataset.repostBadge === 'string' ? JSON.parse(card.dataset.repostBadge) : card.dataset.repostBadge;
                if (badgeData && badgeData.avatars && badgeData.avatars.length > 0) {
                    repostBadgeHtml = renderRepostScatterClusterHtml(badgeData, true);
                }
            } catch (e) {
                console.warn('[VideoManager] Error parsing repost badge dataset:', e);
            }
        }

        const snapItem = document.createElement('div');
        snapItem.className = 'reels-snap-item';
        snapItem.dataset.postId = postId;
        snapItem.dataset.authorName = authorName;
        snapItem.dataset.authorUsername = authorUsername;
        snapItem.dataset.isFollowing = card.dataset.isFollowing === 'true' ? 'true' : 'false';
        snapItem.dataset.authorAvatar = authorAvatar;
        snapItem.dataset.profileUrl = profileUrl;
        snapItem.dataset.unitCode = unitCode;
        snapItem.dataset.timeAgo = timeAgo;
        snapItem.dataset.likes = likeCount;
        snapItem.dataset.isLiked = isLiked ? 'true' : 'false';
        snapItem.dataset.comments = commentCount;
        snapItem.dataset.repostCount = repostCount;
        snapItem.dataset.isReposted = isReposted ? 'true' : 'false';
        snapItem.dataset.videoUrl = videoSrc;
        snapItem.dataset.poster = poster;
        if (video.videoWidth && video.videoHeight) {
            snapItem.dataset.videoWidth = video.videoWidth;
            snapItem.dataset.videoHeight = video.videoHeight;
        } else if (card.dataset.videoWidth && card.dataset.videoHeight) {
            snapItem.dataset.videoWidth = card.dataset.videoWidth;
            snapItem.dataset.videoHeight = card.dataset.videoHeight;
        }
        // Duration drives the blurred-fill decision: a long video would mean a
        // second, full download of the source just to blur it.
        const cardDuration = parseInt(card.dataset.videoDuration || video.dataset.videoDuration || '0', 10);
        if (!isNaN(cardDuration) && cardDuration > 0) {
            snapItem.dataset.videoDuration = cardDuration;
        }
        if (card.classList.contains('landscape-video-container') || card.closest('.landscape-video-container') || card.querySelector('.landscape-video-container') || card.dataset.mediaOrientation === 'landscape') {
            snapItem.dataset.mediaOrientation = 'landscape';
        }
        const rawCaptionText = (card.dataset.caption || card.querySelector('.reel-caption-text')?.textContent || card.querySelector('.post-content-text')?.textContent || card.querySelector('.post-text-body')?.textContent || '').trim();
        snapItem.dataset.caption = rawCaptionText;

        snapItem.innerHTML = `
            <div class="reel-fullscreen-content">
                <!-- Ambient Backdrop -->
                <div class="reel-ambient-backdrop" aria-hidden="true">
                    ${poster ? `<img src="${poster}" class="reel-ambient-img" loading="lazy" decoding="async" alt="">` : '<div class="w-100 h-100 bg-black"></div>'}
                </div>

                <!-- Video Element with on-demand media engine slot initialization -->
                <video class="fullscreen-reel-video w-100 h-100"
                       id="fs-video-${postId}"
                       playsinline loop preload="none"
                       ${isGlobalMuted ? 'muted' : ''}
                       poster="${poster}"
                       data-post-id="${postId}"
                       ${hlsUrl ? `data-hls-url="${hlsUrl}"` : ''}
                       data-video-url="${videoSrc}">
                </video>

                <!-- Live blurred copy of the reel: fills the letterbox band above and
                     below a portrait reel so the player reads as one full-bleed
                     surface. The main video keeps its FULL width (nothing is
                     cropped) — only the empty space is filled. Rendered AFTER the
                     main video on purpose: snapItem.querySelector('video') must
                     keep resolving to the main reel. -->
                <div class="reel-ambient-video-wrap" aria-hidden="true" data-ambient-post-id="${postId}">
                    <div class="reel-ambient-video-scale">
                        <div class="reel-ambient-video-layer">
                            <video class="reel-ambient-video"
                                   muted
                                   loop
                                   playsinline
                                   preload="none"
                                   data-ambient-post-id="${postId}"
                                   data-video-url="${videoSrc}"></video>
                        </div>
                    </div>
                </div>

                <!-- Tap Hitbox for play/pause HUD and heart burst -->
                <button type="button"
                        class="reel-center-hitbox reel-tap-hitbox"
                        data-post-id="${postId}"
                        aria-label="Play or pause reel">
                </button>

                <!-- Transient Play/Pause HUD Indicator -->
                <div class="reel-play-hud" aria-hidden="true">
                    <i class="bi bi-play-fill"></i>
                </div>

                <!-- Central Buffering / Audio-Wait Spinner -->
                <div id="fs-reel-buffer-spinner-${postId}" class="reel-buffering-indicator d-none" aria-hidden="true">
                    <div class="spinner-border spinner-border-sm text-white" role="status">
                        <span class="visually-hidden">Loading…</span>
                    </div>
                    <span class="reel-buffer-text small text-white ms-1.5 fw-medium">Loading…</span>
                </div>

                <!-- Heart Burst Animation Target -->
                <div class="reel-heart-burst" aria-hidden="true">
                    <i class="bi bi-heart-fill"></i>
                </div>

                <!-- Top Scrim -->
                <div class="reel-scrim-top position-absolute top-0 start-0 w-100 d-flex align-items-center justify-content-between" style="z-index: 4;">
                    <div class="d-flex align-items-center gap-2 min-w-0">
                        ${unitHtml}
                    </div>
                    <div class="d-flex align-items-center gap-2">
                        <button type="button"
                                class="reel-top-action-btn reel-options-btn text-white p-1 border-0 bg-transparent"
                                data-post-id="${postId}"
                                data-author="${authorName}"
                                title="Options"
                                aria-label="Options">
                            <i class="bi bi-three-dots-vertical fs-5"></i>
                        </button>
                    </div>
                </div>

                <!-- Floating Right Engagement Rail (Pure floating icons, no backdrop bubbles) -->
                <div class="reel-actions-stack">
                    <div class="reel-action-unit text-center">
                        <button type="button" class="reel-action-btn fs-like-proxy-btn text-white ${isLiked ? 'liked text-danger' : ''}" data-post-id="${postId}" title="Like" aria-label="Like">
                            <i class="bi ${isLiked ? 'bi-heart-fill text-danger' : 'bi-heart'}"></i>
                        </button>
                        <span class="reel-action-label" id="fs-reel-like-count-${postId}">${likeCount}</span>
                    </div>

                    <div class="reel-action-unit text-center">
                        <button type="button" class="reel-action-btn text-white reel-comment-trigger" data-post-id="${postId}" title="Comment" aria-label="Comment">
                            <i class="bi bi-chat-dots-fill"></i>
                        </button>
                        <span class="reel-action-label" id="fs-reel-comment-count-${postId}">${commentCount}</span>
                    </div>

                    <div class="reel-action-unit text-center">
                        <button type="button" class="reel-action-btn fs-repost-proxy-btn text-white ${isReposted ? 'text-primary is-reposted' : ''}" data-post-id="${postId}" title="${isReposted ? 'Reposted' : 'Repost'}" aria-label="${isReposted ? 'Undo repost' : 'Repost'}">
                            <i class="bi ${isReposted ? 'bi-repeat text-primary' : 'bi-repeat'}"></i>
                        </button>
                        <span class="reel-action-label" id="fs-reel-repost-count-${postId}">${repostCount}</span>
                        <span class="reel-action-label fs-repost-state-label ${isReposted ? '' : 'd-none'}" id="fs-reel-repost-state-${postId}">Reposted</span>
                    </div>

                    <div class="reel-action-unit text-center">
                        <button type="button" class="reel-action-btn text-white" data-bs-toggle="modal" data-bs-target="#globalShareModal" data-post-id="${postId}" data-post-url="/post/${postId}/" data-video-url="${videoSrc}" data-poster-url="${poster}" data-post-content="${rawCaptionText.replace(/"/g, '&quot;')}" data-reel="true" title="Share" aria-label="Share">
                            <i class="bi bi-share-fill"></i>
                        </button>
                        <span class="reel-action-label">Share</span>
                    </div>

                    <div class="reel-action-unit text-center">
                        <button type="button" class="reel-action-btn reel-mute-btn text-white" data-action="mute-toggle" title="${isGlobalMuted ? 'Unmute' : 'Mute'}" aria-label="${isGlobalMuted ? 'Unmute' : 'Mute'}">
                            <i class="bi ${isGlobalMuted ? 'bi-volume-mute-fill' : 'bi-volume-up-fill'}"></i>
                        </button>
                        <span class="reel-action-label reel-mute-label">${isGlobalMuted ? 'Muted' : 'Sound'}</span>
                    </div>

                    <div class="reel-action-unit reel-vinyl-unit text-center mt-1">
                        <div class="reel-vinyl-disc" data-post-id="${postId}">
                            <img src="${authorAvatar}" class="reel-vinyl-art" alt="${authorName}">
                        </div>
                    </div>
                </div>

                <!-- Bottom Scrim: Creator Identity, Caption, and Audio Pill (Option C) -->
                <div class="reel-scrim-bottom position-absolute bottom-0 start-0 w-100 d-flex flex-column justify-content-end" style="pointer-events: none; z-index: 4;">
                    <div class="reel-scrim-content">
                        <!-- Compact followed-account repost context -->
                        <div id="fs-repost-badge-${postId}" class="reel-fullscreen-repost-badge ${repostBadgeHtml ? '' : 'd-none'}">
                            ${repostBadgeHtml}
                        </div>
                        ${creatorRowHtml}
                        ${captionHtml}
                    </div>
                </div>

                <!-- Bottom Edge Micro-Scrubber -->
                <div class="reel-progress-container position-absolute bottom-0 start-0 w-100" data-post-id="${postId}">
                    <div class="reel-progress-bar" id="fs-reel-progress-${postId}"></div>
                </div>
            </div>
        `;

        const fsVideoEl = snapItem.querySelector('video');
        if (fsVideoEl) {
            fsVideoEl.muted = isGlobalMuted;
            const fsSpinner = snapItem.querySelector('.reel-buffering-indicator');
            const fsProgress = snapItem.querySelector('.reel-progress-container');
            const fsVinyl = snapItem.querySelector('.reel-vinyl-disc');
            wireVideoBufferingListeners(fsVideoEl, snapItem, fsSpinner, fsProgress, fsVinyl);

            // Wire volumechange to keep fullscreen mute button synchronized
            fsVideoEl.addEventListener('volumechange', () => {
                if (isProgrammaticAudioSync) return;
                if (fsVideoEl._isAutoplayMutedFallback) return;
                if (fsVideoEl.classList.contains('reels-carousel-video') || fsVideoEl.closest('.reels-carousel-shelf')) return;
                syncMuteButtons(fsVideoEl.muted);
                if (fsVideoEl.muted !== isGlobalMuted) {
                    setGlobalMute(fsVideoEl.muted);
                }
            });

            fsVideoEl.addEventListener('playing', () => {
                triggerPlayHud(snapItem, true);
                if (fsVinyl) fsVinyl.classList.add('is-playing');
                syncMuteButtons(fsVideoEl.muted);
            });
            fsVideoEl.addEventListener('pause', () => {
                if (!fsVideoEl.seeking && !fsVideoEl.ended) {
                    triggerPlayHud(snapItem, false);
                    if (fsVinyl) fsVinyl.classList.remove('is-playing');
                }
            });
        }

        adaptSnapItemOrientation(snapItem, card);
        return snapItem;
    }

    let currentDesktopRailPostId = null;
    let sideRailWebSocket = null;
    let sideRailWsPostId = null;

    // Warm HTML cache for the rail's comment stream. `prefetchReelComments`
    // pre-fills it for the NEXT reel while you're still watching the current
    // one, so a swipe paints the comments instantly instead of showing the
    // skeleton. Insertion order doubles as the eviction order (Map API).
    const railCommentsHtmlCache = new Map();
    const RAIL_COMMENTS_CACHE_MAX = 16;

    function cacheRailComments(postId, html) {
        if (!postId || !html) return;
        if (!railCommentsHtmlCache.has(postId) && railCommentsHtmlCache.size >= RAIL_COMMENTS_CACHE_MAX) {
            const oldest = railCommentsHtmlCache.keys().next().value;
            railCommentsHtmlCache.delete(oldest);
        }
        railCommentsHtmlCache.delete(postId); // refresh insertion order on rewrite
        railCommentsHtmlCache.set(postId, html);
    }

    function prefetchReelComments(postId) {
        if (!postId || railCommentsHtmlCache.has(postId)) return;
        if (!state.isFullScreenActive || !isReelDesktopMode()) return;
        fetch(`/post/${postId}/?show_all=1`, { headers: { 'HX-Request': 'true' } })
            .then(res => (res && res.ok ? res.text() : null))
            .then(html => {
                if (html) cacheRailComments(postId, html);
            })
            .catch(() => { /* warm miss — the normal loader path will fetch */ });
    }

    function closeSideRailWebSocket() {
        if (sideRailWebSocket) {
            try {
                sideRailWebSocket.onclose = null;
                sideRailWebSocket.onerror = null;
                sideRailWebSocket.onmessage = null;
                sideRailWebSocket.close();
            } catch (e) {}
            sideRailWebSocket = null;
            sideRailWsPostId = null;
        }
    }

    function initSideRailWebSocket(postId) {
        postId = postId ? String(postId).trim() : '';
        if (!postId || postId === 'null' || postId === 'undefined') return;
        if (sideRailWebSocket && sideRailWsPostId === postId &&
            (sideRailWebSocket.readyState === WebSocket.CONNECTING || sideRailWebSocket.readyState === WebSocket.OPEN)) {
            return;
        }
        closeSideRailWebSocket();

        sideRailWsPostId = postId;
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/post/${postId}/comments/`;

        try {
            const ws = new WebSocket(wsUrl);
            sideRailWebSocket = ws;

            ws.onopen = function() {
                console.log(`[DesktopSideRail] Live comments WebSocket connected for post ${postId}`);
            };

            ws.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    handleSideRailWsMessage(data, postId);
                } catch (err) {
                    console.warn('[DesktopSideRail] WS message error:', err);
                }
            };

            ws.onerror = function(err) {
                console.warn('[DesktopSideRail] WS error for post', postId, err);
            };

            ws.onclose = function() {
                if (sideRailWebSocket === ws && sideRailWsPostId === postId) {
                    sideRailWebSocket = null;
                    sideRailWsPostId = null;
                }
            };
        } catch (e) {
            console.warn('[DesktopSideRail] Could not initialize WebSocket:', e);
        }
    }

    function handleSideRailWsMessage(data, postId) {
        if (!data) return;

        // 1. Comment Like Update
        if (data.type === 'comment_like_update' && data.comment_id) {
            const commentEl = document.getElementById(`comment-${data.comment_id}`);
            if (commentEl) {
                const likeBtn = commentEl.querySelector('[data-action="like"]');
                if (likeBtn) {
                    const countSpan = likeBtn.querySelector('span');
                    if (countSpan) countSpan.textContent = data.likes_count;
                }
            }
            return;
        }

        // 2. New Comment Live Push
        if (data.type === 'new_comment' && data.comment) {
            const comment = data.comment;
            if (document.getElementById(`comment-${comment.id}`)) {
                return; // Already rendered
            }

            const commentsList = document.getElementById('fsRailCommentsList');
            if (!commentsList) return;

            // Remove empty state if present
            const emptyState = commentsList.querySelector('.comments-empty');
            if (emptyState) emptyState.remove();

            if (comment.parent_comment_id) {
                // Threaded reply
                if (typeof CommentManager !== 'undefined' && typeof CommentManager.addReplyToDom === 'function') {
                    CommentManager.addReplyToDom(comment.parent_comment_id, comment);
                    if (typeof CommentManager.updateReplyCount === 'function') {
                        CommentManager.updateReplyCount(comment.parent_comment_id, 1);
                    }
                } else {
                    let repliesContainer = document.getElementById(`replies-${comment.parent_comment_id}`) ||
                                           commentsList.querySelector(`#replies-${comment.parent_comment_id}`);
                    if (!repliesContainer) {
                        const parentEl = document.getElementById(`comment-${comment.parent_comment_id}`);
                        if (parentEl) {
                            repliesContainer = document.createElement('div');
                            repliesContainer.id = `replies-${comment.parent_comment_id}`;
                            repliesContainer.className = 'comment-replies expanded ms-4 mt-2 ps-2 border-start';
                            parentEl.appendChild(repliesContainer);
                        }
                    }
                    if (repliesContainer) {
                        const replyHtml = renderSideRailCommentHtml(comment, true);
                        repliesContainer.insertAdjacentHTML('beforeend', replyHtml);
                    }
                }
            } else {
                // Top-level comment
                let stream = commentsList.querySelector('.comment-stream') || commentsList;
                const commentHtml = (typeof CommentRenderer !== 'undefined' && typeof CommentRenderer.renderComment === 'function')
                    ? CommentRenderer.renderComment(comment, { isReply: false, nestingLevel: 0 })
                    : renderSideRailCommentHtml(comment, false);

                stream.insertAdjacentHTML('afterbegin', commentHtml);
            }

            if (window.htmx) {
                window.htmx.process(commentsList);
            }
            initSheetCommentsInteractions(commentsList, postId);

            // Increment all comment counters
            const railCommentCount = document.getElementById('fsRailCommentCount');
            const fsCommentCount = document.getElementById('fsDesktopCommentCount');
            let curN = parseInt(railCommentCount?.textContent || '0', 10) || 0;
            let newN = curN + 1;
            if (railCommentCount) railCommentCount.textContent = newN;
            if (fsCommentCount) fsCommentCount.textContent = newN;

            const inlinePostCount = document.getElementById(`comment-count-${postId}`);
            const inlineReelCount = document.getElementById(`reel-comment-count-${postId}`);
            const fsReelCount = document.getElementById(`fs-reel-comment-count-${postId}`);
            if (inlinePostCount) inlinePostCount.textContent = newN;
            if (inlineReelCount) inlineReelCount.textContent = newN;
            if (fsReelCount) fsReelCount.textContent = newN;
        }
    }

    function renderSideRailCommentHtml(comment, isReply) {
        const authorName = comment.author?.full_name || comment.author?.username || 'User';
        const authorUsername = comment.author?.username || 'user';
        const avatar = comment.author?.profile_pic || '/static/images/default_avatar.png';
        const content = (comment.content || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const timeAgo = 'just now';

        let attachmentHtml = '';
        if (comment.attachment_type) {
            const url = comment.attachment_url || comment.attachment_image || '';
            if (url) {
                if (comment.attachment_type === 'gif') {
                    attachmentHtml = `<div class="comment-rendered-attachment"><img src="${url}" class="comment-gif-image" alt="GIF" loading="lazy"></div>`;
                } else {
                    attachmentHtml = `<div class="comment-rendered-attachment"><img src="${url}" class="comment-sticker-image" alt="Sticker" loading="lazy" onclick="if(window.openFullscreenImage) window.openFullscreenImage(this.src);"></div>`;
                }
            }
        }

        return `
            <div class="comment-item ${isReply ? 'comment-reply ms-4' : ''}" id="comment-${comment.id}"
                 data-comment-id="${comment.id}"
                 data-author-id="${comment.author?.id || ''}"
                 data-parent-comment-id="${comment.parent_comment_id || ''}"
                 data-reply-count="${comment.reply_count || 0}">
                <a href="/users/user/${authorUsername}/"
                   hx-get="/users/user/${authorUsername}/"
                   hx-target="#page-content-target"
                   hx-swap="innerHTML"
                   hx-push-url="true"
                   class="comment-avatar-link">
                    <img src="${avatar}" alt="${authorName}" class="comment-avatar" loading="lazy" onerror="this.src='/static/images/default_avatar.png'">
                </a>
                <div class="comment-content">
                    <div class="comment-header">
                        <a href="/users/user/${authorUsername}/"
                           hx-get="/users/user/${authorUsername}/"
                           hx-target="#page-content-target"
                           hx-swap="innerHTML"
                           hx-push-url="true"
                           class="comment-author-link fw-bold text-decoration-none">
                            ${authorName}
                        </a>
                        <span class="comment-timestamp">${timeAgo}</span>
                    </div>
                    <div class="comment-body" id="comment-body-${comment.id}">
                        ${content ? `<div>${content}</div>` : ''}
                        ${attachmentHtml}
                    </div>
                    <div class="comment-actions">
                        <button class="comment-action" data-action="like" data-comment-id="${comment.id}" aria-label="Like comment">
                            <i class="bi bi-heart"></i>
                            <span>${comment.likes_count || 0}</span>
                        </button>
                        <button class="comment-action" data-action="reply" data-comment-id="${comment.id}" aria-label="Reply to comment">
                            <i class="bi bi-chat-dots"></i>
                            Reply
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    // ============================================================================
    // DESKTOP IMMERSION HELPERS (>=992px): idle doze + one-time rail tip.
    //
    // 1) Idle dozer: after ~4.5s with no input while a reel is actually playing,
    //    the furniture layer (tools column + author scrim) whispers to ~25%.
    //    Any input snaps it back. The video, the card and the comment rail are
    //    NEVER hidden — immersion comes from visual quiet, not confiscation.
    // 2) Immersive tip: one-time toast — if the user advances 3+ reels with the
    //    rail open and no comment interaction, teach the collapse affordance.
    //    Marked "seen" in localStorage; never shown twice.
    // ============================================================================
    const REEL_RAIL_TIP_KEY = 'pwaninetReelsRailTipSeen';
    const REEL_DOZE_DELAY_MS = 4500;

    function isReelDesktopMode() {
        return window.innerWidth >= 992;
    }

    function setupReelIdleDozer() {
        const overlay = document.getElementById('fullscreenReelsOverlay');
        const layout = document.querySelector('.reel-player-layout');
        if (!overlay || !layout) return function () {};

        let dozeTimer = null;

        function shouldDoze() {
            if (!isReelDesktopMode()) return false;
            // Never dim the room while the first-run keyboard coach is up —
            // a dimmed cheat-sheet teaches nobody anything.
            const coach = document.getElementById('reelKbCoach');
            if (coach && coach.classList.contains('is-visible')) return false;
            const active = getActiveSnapItem();
            const vid = active ? active.querySelector('video') : null;
            return !!vid && !vid.paused && !vid.ended;
        }

        function doze() {
            if (shouldDoze()) layout.classList.add('is-dozing');
        }

        function wake() {
            if (dozeTimer) { clearTimeout(dozeTimer); dozeTimer = null; }
            layout.classList.remove('is-dozing');
            dozeTimer = setTimeout(doze, REEL_DOZE_DELAY_MS);
        }

        const activityEvents = ['mousemove', 'mousedown', 'keydown', 'wheel', 'touchstart', 'pointerdown'];
        activityEvents.forEach(ev => overlay.addEventListener(ev, wake, { passive: true }));
        // Resuming playback wakes the room, even without pointer motion
        overlay.addEventListener('play', wake, true);
        // Keyboard nav lands on <body> (nothing inside the overlay holds focus),
        // so the overlay's own keydown listener never sees it — wake from the
        // document too, or arrow-key browsing never lifts the chrome.
        const onDocKeydown = () => { if (state.isFullScreenActive) wake(); };
        document.addEventListener('keydown', onDocKeydown, { passive: true });

        dozeTimer = setTimeout(doze, REEL_DOZE_DELAY_MS);

        return function teardown() {
            if (dozeTimer) clearTimeout(dozeTimer);
            dozeTimer = null;
            activityEvents.forEach(ev => overlay.removeEventListener(ev, wake));
            overlay.removeEventListener('play', wake, true);
            document.removeEventListener('keydown', onDocKeydown);
            layout.classList.remove('is-dozing');
        };
    }

    function ensureImmersiveRailTip() {
        let tip = document.getElementById('reelImmersiveTip');
        if (tip) return tip;
        const stage = document.querySelector('.reel-desktop-stage');
        if (!stage) return null;
        tip = document.createElement('div');
        tip.id = 'reelImmersiveTip';
        tip.className = 'reel-immersive-tip';
        tip.innerHTML = [
            '<i class="bi bi-layout-sidebar-inset" aria-hidden="true"></i>',
            '<span>' + (window.gettext ? gettext('Collapse the rail for full-cinema mode') : 'Collapse the rail for full-cinema mode') + '</span>',
            '<button type="button" class="btn btn-sm btn-primary rounded-pill px-3 reel-immersive-tip-cta">' + (window.gettext ? gettext('Collapse') : 'Collapse') + '</button>'
        ].join('');
        tip.querySelector('.reel-immersive-tip-cta').addEventListener('click', function (e) {
            e.stopPropagation();
            e.preventDefault();
            tip.classList.remove('is-visible');
            toggleDesktopCommentRail(false);
        });
        stage.appendChild(tip);
        return tip;
    }

    function setupImmersiveTipCounter() {
        const viewport = document.getElementById('reelsSnapViewport');
        const rail = document.getElementById('reelDesktopSideRail');
        if (!viewport || !rail) return function () {};

        let lastIdx = Math.round(viewport.scrollTop / (viewport.clientHeight || 1));
        let advances = 0;
        let tipTimer = null;

        function onScroll() {
            try {
                if (localStorage.getItem(REEL_RAIL_TIP_KEY)) { detach(); return; }
            } catch (_) { return; }
            if (!isReelDesktopMode()) return;

            const h = viewport.clientHeight || 1;
            const idx = Math.round(viewport.scrollTop / h);
            if (idx > lastIdx) advances++;
            if (idx !== lastIdx) lastIdx = idx;

            // 3+ consecutive reels with the rail open and no comment input = browsing signal
            if (advances >= 3 && !rail.classList.contains('rail-collapsed')) {
                const tip = ensureImmersiveRailTip();
                if (tip) {
                    tip.classList.add('is-visible');
                    if (tipTimer) clearTimeout(tipTimer);
                    tipTimer = setTimeout(() => tip.classList.remove('is-visible'), 6500);
                }
                try { localStorage.setItem(REEL_RAIL_TIP_KEY, '1'); } catch (_) {}
                detach();
            }
        }

        function detach() {
            viewport.removeEventListener('scroll', onScroll);
            if (tipTimer) clearTimeout(tipTimer);
        }

        viewport.addEventListener('scroll', onScroll, { passive: true });
        return detach;
    }

    // ============================================================================
    // KEYBOARD EDUCATION — first-run coach card + always-available "?"/panel.
    // Same pattern as the immersive rail tip: localStorage-gated, one-time,
    // appended to the stage so the existing doze/teardown machinery covers it.
    // ============================================================================

    const REEL_KB_COACH_KEY = 'pwaninetReelsKbCoachSeen';
    let reelKbPanelOpen = false;
    let reelKbCoachKeyTimer = null;

    function reelKbRowsHtml() {
        const rows = [
            ['Space', 'Play / pause'],
            ['\u2191 / \u2193', 'Previous / next reel'],
            ['M', 'Mute / unmute'],
            ['C', 'Cinema mode (hide rail)'],
            ['Esc', 'Close player']
        ];
        return '<div class="reel-kb-rows">' + rows.map(function (r) {
            var label = window.gettext ? gettext(r[1]) : r[1];
            return '<div class="reel-kb-row"><kbd class="reel-kb-key">' + r[0] + '</kbd><span class="reel-kb-desc">' + label + '</span></div>';
        }).join('') + '</div>';
    }

    function ensureKbHelpDom() {
        const stage = document.querySelector('.reel-desktop-stage');
        if (!stage) return null;

        let coach = document.getElementById('reelKbCoach');
        if (!coach) {
            coach = document.createElement('div');
            coach.id = 'reelKbCoach';
            coach.className = 'reel-kb-coach';
            coach.innerHTML = [
                '<button type="button" class="reel-kb-coach-close" aria-label="Dismiss"><i class="bi bi-x-lg"></i></button>',
                '<div class="reel-kb-coach-title"><i class="bi bi-keyboard" aria-hidden="true"></i><span>' + (window.gettext ? gettext('Browse with your keyboard') : 'Browse with your keyboard') + '</span></div>',
                reelKbRowsHtml()
            ].join('');
            coach.addEventListener('click', function (e) { e.stopPropagation(); });
            coach.querySelector('.reel-kb-coach-close').addEventListener('click', function () { dismissReelKbCoach(); });
            stage.appendChild(coach);
        }

        let panel = document.getElementById('reelKbPanel');
        if (!panel) {
            panel = document.createElement('div');
            panel.id = 'reelKbPanel';
            panel.className = 'reel-kb-panel';
            panel.innerHTML = reelKbRowsHtml();
            panel.addEventListener('click', function (e) { e.stopPropagation(); });
            stage.appendChild(panel);
        }

        return { coach: coach, panel: panel };
    }

    function hideReelKbCoach() {
        // Teardown-safe: just vanish. MUST NOT mark the coach as "seen" —
        // teardown also runs when an open supersedes a previous open, and that
        // would suppress the coach before it ever displayed.
        const coach = document.getElementById('reelKbCoach');
        if (coach) coach.classList.remove('is-visible');
        if (reelKbCoachKeyTimer) { clearTimeout(reelKbCoachKeyTimer); reelKbCoachKeyTimer = null; }
    }

    function dismissReelKbCoach() {
        hideReelKbCoach();
        try { localStorage.setItem(REEL_KB_COACH_KEY, '1'); } catch (_) {}
    }

    function maybeShowReelKbCoach() {
        if (!isReelDesktopMode()) return;
        try { if (localStorage.getItem(REEL_KB_COACH_KEY)) return; } catch (_) { return; }
        const dom = ensureKbHelpDom();
        if (!dom) return;

        dom.coach.classList.add('is-visible');
        try { localStorage.setItem(REEL_KB_COACH_KEY, '1'); } catch (_) {}

        // Pressing any key IS the lesson working — dismiss on the first one.
        const onFirstKey = function () {
            dismissReelKbCoach();
            document.removeEventListener('keydown', onFirstKey, true);
        };
        document.addEventListener('keydown', onFirstKey, true);
        reelKbCoachKeyTimer = setTimeout(onFirstKey, 9000);
    }

    function toggleReelKbPanel(force) {
        const dom = ensureKbHelpDom();
        if (!dom) return;
        const helpBtn = document.getElementById('reelKbHelpBtn');
        reelKbPanelOpen = (typeof force === 'boolean') ? force : !reelKbPanelOpen;
        dom.panel.classList.toggle('is-open', reelKbPanelOpen);
        if (helpBtn) helpBtn.classList.toggle('is-active', reelKbPanelOpen);
    }

    function setupReelKbEducation() {
        // Idempotent document-level wiring (button toggle + outside-click close)
        if (window._reelKbHelpBound) return;
        window._reelKbHelpBound = true;
        document.addEventListener('click', function (e) {
            if (!state.isFullScreenActive) return;
            if (e.target.closest && e.target.closest('#reelKbHelpBtn')) {
                e.preventDefault();
                toggleReelKbPanel();
                return;
            }
            if (reelKbPanelOpen && !(e.target.closest && e.target.closest('#reelKbPanel'))) {
                toggleReelKbPanel(false);
            }
        });
    }

    function setupReelDesktopFx() {
        // ...teardown chains all helpers into one call
        setupReelKbEducation();
        const t1 = setupReelIdleDozer();
        const t2 = setupImmersiveTipCounter();
        return function teardown() {
            try { t1(); } catch (_) {}
            try { t2(); } catch (_) {}
            toggleReelKbPanel(false);
            hideReelKbCoach();
        };
    }

    function toggleDesktopCommentRail(forceOpen) {
        const rail = document.getElementById('reelDesktopSideRail');
        if (!rail) return;

        const isCurrentlyCollapsed = rail.classList.contains('rail-collapsed');
        const shouldOpen = (typeof forceOpen === 'boolean') ? forceOpen : isCurrentlyCollapsed;

        const layout = document.querySelector('.reel-player-layout');
        const toggleWrapper = document.getElementById('reelCommentRailToggleWrapper');

        if (shouldOpen) {
            rail.classList.remove('rail-collapsed');
            if (layout) layout.classList.remove('rail-collapsed');
            if (toggleWrapper) toggleWrapper.classList.remove('is-visible');
            const input = document.getElementById('fsRailCommentInput');
            if (input) {
                setTimeout(() => input.focus(), 120);
            }
        } else {
            rail.classList.add('rail-collapsed');
            if (layout) layout.classList.add('rail-collapsed');
            if (toggleWrapper) toggleWrapper.classList.add('is-visible');
        }

        // Smoothly adjust exterior action buttons positioning
        const repositionActive = () => {
            const activeSnap = getActiveSnapItem();
            if (activeSnap) {
                adaptSnapItemOrientation(activeSnap);
            }
        };

        setTimeout(repositionActive, 100);
        setTimeout(repositionActive, 320);
    }

    function refreshDesktopSideRailComments() {
        const postId = currentDesktopRailPostId;
        if (!postId) return;

        const refreshBtn = document.getElementById('fsRailRefreshBtn');
        const icon = refreshBtn?.querySelector('i');
        if (icon) icon.classList.add('spinning');
        if (refreshBtn) refreshBtn.disabled = true;

        const commentsList = document.getElementById('fsRailCommentsList');
        const commentsLoading = document.getElementById('fsRailCommentsLoading');

        fetch(`/post/${postId}/?show_all=1`, {
            headers: { 'HX-Request': 'true' }
        })
        .then(res => res.text())
        .then(html => {
            if (icon) icon.classList.remove('spinning');
            if (refreshBtn) refreshBtn.disabled = false;
            if (commentsLoading) commentsLoading.style.display = 'none';
            cacheRailComments(postId, html); // refreshed html supersedes any warm copy

            if (commentsList) {
                commentsList.innerHTML = html;
                reinitHtmxElement(commentsList);
                initSheetCommentsInteractions(commentsList, postId);

                const countFromDom = commentsList.querySelectorAll('.comment-item:not(.comment-reply)').length;
                if (countFromDom > 0) {
                    const railCommentCount = document.getElementById('fsRailCommentCount');
                    const fsCommentCount = document.getElementById('fsDesktopCommentCount');
                    if (railCommentCount && parseInt(railCommentCount.textContent, 10) < countFromDom) {
                        railCommentCount.textContent = countFromDom;
                    }
                    if (fsCommentCount && parseInt(fsCommentCount.textContent, 10) < countFromDom) {
                        fsCommentCount.textContent = countFromDom;
                    }
                }
            }
        })
        .catch(err => {
            console.warn('[DesktopSideRail] Refresh comments error:', err);
            if (icon) icon.classList.remove('spinning');
            if (refreshBtn) refreshBtn.disabled = false;
        });
    }

    function reinitHtmxElement(el) {
        if (!el || !window.htmx) return;
        try {
            window.htmx.process(el);
        } catch (e) {
            console.warn('[VideoManager] HTMX process error:', e);
        }
    }

    function isSnapItemActive(item) {
        if (!state.isFullScreenActive || !item) return false;
        const viewport = document.getElementById('reelsSnapViewport');
        if (!viewport || viewport.children.length === 0) return false;
        const h = viewport.clientHeight || 1;
        const curIdx = Math.round(viewport.scrollTop / h);
        return viewport.children[curIdx] === item;
    }

    function getActiveSnapItem() {
        if (!state.isFullScreenActive) return null;
        const viewport = document.getElementById('reelsSnapViewport');
        if (!viewport || viewport.children.length === 0) return null;
        const h = viewport.clientHeight || 1;
        const curIdx = Math.round(viewport.scrollTop / h);
        return viewport.children[curIdx] || viewport.firstElementChild;
    }

    function adaptSnapItemOrientation(snapItem, initialCard) {
        if (!snapItem) return;
        const content = snapItem.querySelector('.reel-fullscreen-content');
        const video = snapItem.querySelector('video');
        if (!content || !video) return;

        // On mobile (<992px): ALWAYS full-bleed edge-to-edge 100% width and 100% height
        if (window.innerWidth < 992) {
            content.style.width = '';
            content.style.height = '';
            content.style.maxWidth = '';
            content.style.maxHeight = '';
            content.style.aspectRatio = '';
            content.classList.remove('is-portrait', 'is-landscape', 'is-square');
            return;
        }

        function checkMetadata() {
            if (window.innerWidth < 992) {
                content.style.width = '';
                content.style.height = '';
                content.style.maxWidth = '';
                content.style.maxHeight = '';
                content.style.aspectRatio = '';
                content.classList.remove('is-portrait', 'is-landscape', 'is-square');
                return;
            }

            const vw = video.videoWidth || (initialCard && initialCard.dataset.videoWidth ? parseInt(initialCard.dataset.videoWidth, 10) : 0) || (snapItem.dataset.videoWidth ? parseInt(snapItem.dataset.videoWidth, 10) : 0);
            const vh = video.videoHeight || (initialCard && initialCard.dataset.videoHeight ? parseInt(initialCard.dataset.videoHeight, 10) : 0) || (snapItem.dataset.videoHeight ? parseInt(snapItem.dataset.videoHeight, 10) : 0);
            if (!vw || !vh) {
                const isCardLandscape = (initialCard && (initialCard.classList.contains('landscape-video-container') || initialCard.closest('.landscape-video-container') || initialCard.dataset.mediaOrientation === 'landscape')) ||
                                        (snapItem && (snapItem.dataset.mediaOrientation === 'landscape' || snapItem.classList.contains('is-landscape')));
                if (isCardLandscape) {
                    content.classList.remove('is-portrait', 'is-square');
                    content.classList.add('is-landscape');
                    content.style.aspectRatio = '16 / 9';
                }
                return;
            }

            const ratio = vw / vh;
            content.classList.remove('is-portrait', 'is-landscape', 'is-square');

            const stage = document.querySelector('.reel-desktop-stage');
            const stageWidth = stage ? stage.clientWidth : window.innerWidth;
            const stageHeight = stage ? stage.clientHeight : window.innerHeight;

            // `clientWidth`/`clientHeight` include the stage paddings, so subtract them
            // explicitly: 120px horizontal padding (104px fixed tools strip + 16px gutter)
            // + 32px breathing room, and 24px vertical padding (12px top + 12px bottom).
            const availableWidth = Math.max(260, stageWidth - 152);
            const availableHeight = Math.max(300, stageHeight - 24);

            if (ratio >= 1.15) {
                // True landscape video
                content.classList.add('is-landscape');
                content.style.aspectRatio = `${vw} / ${vh}`;
                const maxWFromHeight = availableHeight * ratio;
                const finalW = Math.min(availableWidth, maxWFromHeight);
                content.style.width = `${Math.round(finalW)}px`;
                content.style.maxWidth = `${Math.round(availableWidth)}px`;
                content.style.height = `${Math.round(finalW / ratio)}px`;
                content.style.maxHeight = `${Math.round(availableHeight)}px`;
            } else if (ratio >= 0.88 && ratio < 1.15) {
                // Square video
                content.classList.add('is-square');
                content.style.aspectRatio = '1 / 1';
                const sz = Math.min(availableWidth, availableHeight);
                content.style.width = `${Math.round(sz)}px`;
                content.style.height = `${Math.round(sz)}px`;
                content.style.maxWidth = `${Math.round(availableWidth)}px`;
                content.style.maxHeight = `${Math.round(availableHeight)}px`;
            } else {
                // Portrait 9:16 video
                content.classList.add('is-portrait');
                content.style.aspectRatio = `${vw} / ${vh}`;
                const finalH = availableHeight;
                const finalW = Math.min(availableWidth, finalH * ratio);
                content.style.height = `${Math.round(finalH)}px`;
                content.style.width = `${Math.round(finalW)}px`;
                content.style.maxWidth = `${Math.round(availableWidth)}px`;
            }
        }

        checkMetadata();

        if (!video.videoWidth || !video.videoHeight) {
            video.addEventListener('loadedmetadata', checkMetadata, { once: true });
            video.addEventListener('canplay', checkMetadata, { once: true });
        }
    }

    // ============================================================================
    // LIVE BLURRED FILL (LETTERBOX FILLER) FOR THE MOBILE FULLSCREEN PLAYER
    // ---------------------------------------------------------------------------
    // A portrait reel keeps its FULL width inside the full-screen stage, which
    // leaves a band above and below. That band is filled with a second, muted
    // copy of the SAME video, blurred and scaled up, so the surface reads as
    // one full-bleed reel instead of "video + padding".
    //
    // Rules that keep this cheap and safe:
    //  - only the ACTIVE reel ever gets a live copy (one extra decoder max)
    //  - mobile only: on desktop the reel card fills the stage, nothing to fill
    //  - long videos (> 120s) keep the cheap static poster fill, so the filler
    //    never triggers a second download of a whole long-form video
    //  - the copy is muted forever and is ignored by the global audio manager
    //  - the copy is excluded from every global video sweep (mute sync, pause
    //    all, playback state, auto-scroll) so it can never take over the reel
    // ============================================================================

    /**
     * Fill mode switch, live-editable from the console:
     *   window.VASTO_REEL_FILL_MODE = 'live'   → live blurred copy (default)
     *   window.VASTO_REEL_FILL_MODE = 'auto'   → live copy for MP4 reels only (HLS keeps the poster)
     *   window.VASTO_REEL_FILL_MODE = 'poster' → never live copy
     */
    function getAmbientFillMode() {
        const mode = (typeof window !== 'undefined' && window.VASTO_REEL_FILL_MODE) || 'live';
        return mode === 'live' || mode === 'auto' || mode === 'poster' ? mode : 'live';
    }

    function getAmbientFillVideo(snapItem) {
        if (!snapItem) return null;
        return snapItem.querySelector('video.reel-ambient-video');
    }

    function getAmbientFillWrap(snapItem) {
        if (!snapItem) return null;
        return snapItem.querySelector('.reel-ambient-video-wrap');
    }

    /**
     * Attach the blurred copy and lock it to the main reel's playback.
     * Called for the active reel only.
     */
    function attachAmbientFill(snapItem) {
        const ambient = getAmbientFillVideo(snapItem);
        const main = snapItem ? snapItem.querySelector('video.fullscreen-reel-video') : null;
        if (!ambient || !main) return;

        // Desktop stage: the reel card already fills the area — no fill needed.
        if (typeof window !== 'undefined' && window.innerWidth >= 992) {
            detachAmbientFill(snapItem);
            return;
        }

        const mode = getAmbientFillMode();
        if (mode === 'poster') return;

        const isHls = !!(main.dataset && main.dataset.hlsUrl);

        // Duration guard: the filler is a SECOND copy of the source. Never pull
        // a whole long-form video just to blur it — long videos keep the
        // (cheap, static) poster fill instead.
        const duration = parseInt((snapItem.dataset && snapItem.dataset.videoDuration) || '0', 10);
        if (!isNaN(duration) && duration > 120) return;

        // 'auto' keeps HLS reels on the poster fill (a second HLS session would
        // double the segment traffic on mobile data). 'live' uses the MP4
        // fallback for the filler, which is cheap for short reels.
        if (mode === 'auto' && isHls) return;

        const src = ambient.dataset.videoUrl;
        if (!src) return;

        // 1. Wire the listeners ONCE per element (the media can be attached and
        //    detached repeatedly as the reel scrolls in and out of the window).
        if (ambient.dataset.ambientWired !== 'true') {
            ambient.dataset.ambientWired = 'true';

            ambient.addEventListener('loadeddata', () => {
                const wrap = getAmbientFillWrap(snapItem);
                if (wrap) wrap.classList.add('is-live');
                syncAmbientFill(snapItem);
                if (main && !main.paused) {
                    ambient.play().catch(() => {});
                }
            });

            // Lock the copy to the main playback.
            main.addEventListener('play', () => {
                ambient.play().catch(() => {});
                syncAmbientFill(snapItem);
            });
            main.addEventListener('pause', () => {
                ambient.pause();
            });
            main.addEventListener('seeked', () => syncAmbientFill(snapItem));
            main.addEventListener('timeupdate', () => {
                if (ambient.paused) return;
                if (Math.abs(ambient.currentTime - main.currentTime) > 0.3) {
                    try { ambient.currentTime = main.currentTime; } catch (_) {}
                }
            });
        }

        // 2. Attach the media (only for the active reel → one extra decoder).
        if (ambient.dataset.ambientAttached !== 'true') {
            ambient.dataset.ambientAttached = 'true';
            ambient.muted = true;
            ambient.src = src;
            try { ambient.load(); } catch (_) {}
        }

        syncAmbientFill(snapItem);
        if (!main.paused) {
            ambient.play().catch(() => {});
        }
    }

    /** Re-align the blurred copy with the main reel. */
    function syncAmbientFill(snapItem) {
        const ambient = getAmbientFillVideo(snapItem);
        const main = snapItem ? snapItem.querySelector('video.fullscreen-reel-video') : null;
        if (!ambient || !main) return;
        if (ambient.dataset.ambientAttached !== 'true') return;
        if (ambient.readyState < 1) return;
        if (Math.abs(ambient.currentTime - main.currentTime) > 0.25) {
            try { ambient.currentTime = main.currentTime; } catch (_) {}
        }
    }

    /** Release the blurred copy (decoder + network) for a reel that scrolled away. */
    function detachAmbientFill(snapItem) {
        const ambient = getAmbientFillVideo(snapItem);
        if (!ambient) return;
        try { ambient.pause(); } catch (_) {}
        const wrap = getAmbientFillWrap(snapItem);
        if (wrap) wrap.classList.remove('is-live');
        if (ambient.dataset.ambientAttached === 'true') {
            ambient.dataset.ambientAttached = 'false';
            try {
                ambient.removeAttribute('src');
                ambient.src = '';
                ambient.load();
            } catch (_) {}
        }
    }

    function updateFullscreenNavButtons(target) {
        const viewport = document.getElementById('reelsSnapViewport');
        const navPrev = document.getElementById('reelsNavPrev');
        const navNext = document.getElementById('reelsNavNext');
        if (!viewport || !navPrev || !navNext) return;

        const items = Array.from(viewport.children);
        const total = items.length;
        if (total <= 1 || !window.isAuthenticated) {
            navPrev.classList.add('d-none');
            navNext.classList.add('d-none');
            return;
        }

        let currentIndex = 0;
        if (typeof target === 'number') {
            currentIndex = target;
        } else if (target && target instanceof Element) {
            const idx = items.indexOf(target);
            if (idx !== -1) {
                currentIndex = idx;
            } else {
                const h = viewport.clientHeight || 1;
                currentIndex = Math.round(viewport.scrollTop / h);
            }
        } else {
            const h = viewport.clientHeight || 1;
            currentIndex = Math.round(viewport.scrollTop / h);
        }

        currentIndex = Math.max(0, Math.min(total - 1, currentIndex));

        // If it's the first reel, hide the up button
        if (currentIndex <= 0) {
            navPrev.classList.add('d-none');
        } else {
            navPrev.classList.remove('d-none');
        }

        // If it's the last reel, hide the down button
        if (currentIndex >= total - 1) {
            navNext.classList.add('d-none');
        } else {
            navNext.classList.remove('d-none');
        }
    }

    function updateDesktopSideRail(activeItem) {
        if (activeItem) {
            updateFullscreenNavButtons(activeItem);
        }
        if (!activeItem) return;

        const rawPostId = activeItem.dataset.postId || extractPostId(activeItem);
        const postId = rawPostId ? String(rawPostId).trim() : '';
        if (!postId || postId === 'null' || postId === 'undefined') return;

        const authorName = activeItem.dataset.authorName || 'Author';
        const authorUsername = activeItem.dataset.authorUsername || 'author';
        const authorAvatar = activeItem.dataset.authorAvatar || '/static/images/default_avatar.png';
        const profileUrl = activeItem.dataset.profileUrl || `/users/user/${authorUsername}/`;
        const unitCode = activeItem.dataset.unitCode || '';
        const timeAgo = activeItem.dataset.timeAgo || '';
        const likeCount = activeItem.dataset.likes || '0';
        const isLiked = activeItem.dataset.isLiked === 'true' || activeItem.querySelector('.fs-like-proxy-btn')?.classList.contains('liked');
        const commentCount = activeItem.dataset.comments ||
                             activeItem.querySelector('.reel-action-unit [id^="fs-reel-comment-count-"]')?.textContent?.trim() ||
                             '0';
        const repostCount = activeItem.dataset.repostCount ||
                            activeItem.querySelector('.reel-action-unit [id^="fs-reel-repost-count-"]')?.textContent?.trim() ||
                            '0';
        const isReposted = activeItem.dataset.isReposted === 'true' ||
                           activeItem.querySelector('.fs-repost-proxy-btn')?.classList.contains('is-reposted') ||
                           false;

        // 1. Update Exterior Engagement Stack (Desktop/Tablet Floating Rail)
        const fsLikeBtn = document.getElementById('fsDesktopLikeBtn');
        const fsLikeCount = document.getElementById('fsDesktopLikeCount');
        if (fsLikeBtn) {
            fsLikeBtn.dataset.postId = postId;
            fsLikeBtn.setAttribute('data-post-id', postId);
            fsLikeBtn.classList.toggle('liked', isLiked);
            const icon = fsLikeBtn.querySelector('i');
            if (icon) icon.className = isLiked ? 'bi bi-heart-fill text-danger' : 'bi bi-heart';
        }
        if (fsLikeCount) {
            fsLikeCount.textContent = likeCount;
        }

        const fsCommentBtn = document.getElementById('fsDesktopCommentBtn');
        const fsCommentCount = document.getElementById('fsDesktopCommentCount');
        if (fsCommentBtn) {
            fsCommentBtn.dataset.postId = postId;
            fsCommentBtn.onclick = function(e) {
                if (e) {
                    e.preventDefault();
                    e.stopPropagation();
                }
                toggleDesktopCommentRail(true);
            };
        }
        if (fsCommentCount) {
            fsCommentCount.textContent = commentCount;
        }

        // Wire Reopen Comment Rail Toggle Button (Desktop Stage)
        const railToggleBtn = document.getElementById('reelCommentRailToggleBtn');
        if (railToggleBtn && !railToggleBtn.dataset.bound) {
            railToggleBtn.dataset.bound = 'true';
            railToggleBtn.onclick = function(e) {
                if (e) {
                    e.preventDefault();
                    e.stopPropagation();
                }
                toggleDesktopCommentRail(true);
            };
        }

        const fsRepostBtn = document.getElementById('fsDesktopRepostBtn');
        const fsRepostCount = document.getElementById('fsDesktopRepostCount');
        if (fsRepostBtn) {
            fsRepostBtn.dataset.postId = postId;
            fsRepostBtn.setAttribute('data-post-id', postId);
            fsRepostBtn.classList.toggle('text-primary', isReposted);
            fsRepostBtn.classList.toggle('is-reposted', isReposted);
            fsRepostBtn.title = isReposted ? 'Reposted' : 'Repost';
            fsRepostBtn.setAttribute('aria-label', isReposted ? 'Undo repost' : 'Repost');
            const rIcon = fsRepostBtn.querySelector('i');
            if (rIcon) rIcon.className = isReposted ? 'bi bi-repeat text-primary' : 'bi bi-repeat';
        }
        document.getElementById('fsDesktopRepostState')?.classList.toggle('d-none', !isReposted);
        if (fsRepostCount) {
            fsRepostCount.textContent = repostCount;
        }

        const fsShareBtn = document.getElementById('fsDesktopShareBtn');
        if (fsShareBtn) {
            fsShareBtn.dataset.postId = postId;
            fsShareBtn.setAttribute('data-post-id', postId);
            fsShareBtn.setAttribute('data-post-url', `/post/${postId}/`);
            fsShareBtn.setAttribute('data-video-url', activeItem.dataset.videoUrl || '');
            fsShareBtn.setAttribute('data-poster-url', activeItem.dataset.poster || '');
            fsShareBtn.setAttribute('data-post-content', activeItem.dataset.caption || '');
        }

        const fsBookmarkBtn = document.getElementById('fsDesktopBookmarkBtn');
        const fsBookmarkLabel = document.getElementById('fsDesktopBookmarkLabel');
        if (fsBookmarkBtn) {
            fsBookmarkBtn.dataset.postId = postId;
            const isSaved = window.pwaniSavedPosts ? window.pwaniSavedPosts.isPostSaved(postId) : fsBookmarkBtn.classList.contains('active');
            fsBookmarkBtn.classList.toggle('active', isSaved);
            const icon = fsBookmarkBtn.querySelector('i');
            if (icon) icon.className = isSaved ? 'bi bi-bookmark-fill text-warning' : 'bi bi-bookmark';
            if (fsBookmarkLabel) fsBookmarkLabel.textContent = isSaved ? 'Saved' : 'Save';
            fsBookmarkBtn.onclick = function() {
                const nowSaved = window.pwaniSavedPosts ? window.pwaniSavedPosts.togglePostSaved(postId) : fsBookmarkBtn.classList.toggle('active');
                fsBookmarkBtn.classList.toggle('active', nowSaved);
                if (icon) icon.className = nowSaved ? 'bi bi-bookmark-fill text-warning' : 'bi bi-bookmark';
                if (fsBookmarkLabel) fsBookmarkLabel.textContent = nowSaved ? 'Saved' : 'Save';
            };
        }

        // 2. Update Right Side Rail Header
        const avatarEl = document.getElementById('fsRailAuthorAvatar');
        const nameLink = document.getElementById('fsRailAuthorNameLink');
        const profileLink = document.getElementById('fsRailAuthorProfileLink');
        const nameEl = document.getElementById('fsRailAuthorName');
        const handleEl = document.getElementById('fsRailAuthorHandle');
        const unitEl = document.getElementById('fsRailUnitBadge');
        const timeEl = document.getElementById('fsRailTime');
        const captionEl = document.getElementById('fsRailCaption');
        const soundTextEl = document.getElementById('fsRailSoundText');
        const optionsBtn = document.getElementById('fsRailOptionsBtn');
        const closeBtn = document.getElementById('fsRailCloseBtn');
        const refreshBtn = document.getElementById('fsRailRefreshBtn');

        if (avatarEl) avatarEl.src = authorAvatar;
        if (nameLink) {
            nameLink.href = profileUrl;
            nameLink.setAttribute('hx-get', profileUrl);
            nameLink.setAttribute('hx-target', '#page-content-target');
            nameLink.setAttribute('hx-swap', 'innerHTML');
            nameLink.setAttribute('hx-push-url', 'true');
        }
        if (profileLink) {
            profileLink.href = profileUrl;
            profileLink.setAttribute('hx-get', profileUrl);
            profileLink.setAttribute('hx-target', '#page-content-target');
            profileLink.setAttribute('hx-swap', 'innerHTML');
            profileLink.setAttribute('hx-push-url', 'true');
        }
        if (nameEl) nameEl.textContent = authorName;
        if (handleEl) handleEl.textContent = `@${authorUsername}`;
        if (timeEl) timeEl.textContent = timeAgo || 'recently';
        if (unitEl) {
            unitEl.innerHTML = unitCode ?
                `<span class="badge bg-primary bg-opacity-10 text-primary border-0 ms-1 px-1.5 py-0.5" style="font-size: 10px;">${unitCode}</span>` : '';
        }

        // Wire Rail Header Close Button
        if (closeBtn && !closeBtn.dataset.bound) {
            closeBtn.dataset.bound = 'true';
            closeBtn.onclick = function(e) {
                if (e) {
                    e.preventDefault();
                    e.stopPropagation();
                }
                toggleDesktopCommentRail(false);
            };
        }

        // Wire Rail Comments Refresh Button
        if (refreshBtn && !refreshBtn.dataset.bound) {
            refreshBtn.dataset.bound = 'true';
            refreshBtn.onclick = function(e) {
                if (e) {
                    e.preventDefault();
                    e.stopPropagation();
                }
                refreshDesktopSideRailComments();
            };
        }

        // Caption
        if (captionEl) {
            const rawCaptionHtml = activeItem.querySelector('.reel-caption-text')?.innerHTML ||
                                   activeItem.querySelector('.post-content-text')?.innerHTML ||
                                   activeItem.dataset.caption ||
                                   '';
            const plainText = (activeItem.dataset.caption || activeItem.querySelector('.reel-caption-text')?.textContent || '').trim();

            if (plainText.length > 110 || (plainText.match(/\n/g) || []).length >= 2) {
                captionEl.innerHTML = `
                    <div class="reel-rail-caption-clamp" id="fsRailCaptionInner">${rawCaptionHtml}</div>
                    <button type="button" class="btn btn-link btn-sm p-0 text-muted text-decoration-none mt-1 fw-semibold fs-rail-caption-toggle" style="font-size: 12px;">
                        ... See more
                    </button>
                `;
                const toggleBtn = captionEl.querySelector('.fs-rail-caption-toggle');
                const clampEl = captionEl.querySelector('#fsRailCaptionInner');
                if (toggleBtn && clampEl) {
                    toggleBtn.onclick = function() {
                        const isExp = clampEl.classList.toggle('is-expanded');
                        toggleBtn.textContent = isExp ? 'See less' : '... See more';
                    };
                }
            } else {
                captionEl.innerHTML = rawCaptionHtml;
            }
        }

        if (soundTextEl) {
            soundTextEl.textContent = `Original Audio - ${authorUsername}`;
        }

        if (optionsBtn) {
            optionsBtn.dataset.postId = postId;
            optionsBtn.dataset.author = authorName;
            optionsBtn.onclick = function() {
                openReelQuickTools(postId, optionsBtn);
            };
        }

        // Process HTMX on side rail header links cleanly
        const railHeader = document.querySelector('.reel-rail-header');
        if (railHeader) {
            reinitHtmxElement(railHeader);
        }

        // 3. Update Comments Stream in Side Rail
        const railCommentCount = document.getElementById('fsRailCommentCount');
        if (railCommentCount) railCommentCount.textContent = commentCount;

        if (currentDesktopRailPostId === postId) {
            initSideRailWebSocket(postId);
            return;
        }
        currentDesktopRailPostId = postId;
        initSideRailWebSocket(postId);

        const commentsList = document.getElementById('fsRailCommentsList');
        const commentsLoading = document.getElementById('fsRailCommentsLoading');
        const parentInput = document.getElementById('fsRailParentId');
        const replyBanner = document.getElementById('fsRailReplyBanner');
        const commentInput = document.getElementById('fsRailCommentInput');

        if (parentInput) parentInput.value = '';
        if (replyBanner) replyBanner.classList.add('d-none');
        if (commentInput) {
            commentInput.value = '';
            commentInput.placeholder = 'Add a comment...';
        }

        if (commentsLoading) commentsLoading.style.display = 'block';
        if (commentsList) {
            commentsList.innerHTML = '';
            commentsList.dataset.activePostId = postId;
            initSheetCommentsInteractions(commentsList, postId);
        }

        // Shared paint path: identical for a warm cache hit and a fresh fetch.
        function paintRailComments(html) {
            if (currentDesktopRailPostId !== postId) return;
            if (commentsLoading) commentsLoading.style.display = 'none';
            if (commentsList) {
                commentsList.innerHTML = html;
                reinitHtmxElement(commentsList);
                initSheetCommentsInteractions(commentsList, postId);
                initSideRailWebSocket(postId);
            }
        }

        const cachedCommentsHtml = railCommentsHtmlCache.get(postId);
        if (cachedCommentsHtml) {
            // Warm hit from prefetch (or a previous visit) — skip the network
            // entirely; freshness comes from the live comment WebSocket and the
            // manual refresh button.
            paintRailComments(cachedCommentsHtml);
        } else {
        fetch(`/post/${postId}/?show_all=1`, {
            headers: { 'HX-Request': 'true' }
        })
        .then(res => res.text())
        .then(html => {
            if (currentDesktopRailPostId !== postId) return;
            cacheRailComments(postId, html);
            paintRailComments(html);
        })
        .catch(err => {
            if (currentDesktopRailPostId !== postId) return;
            console.warn('[DesktopSideRail] Failed to load comments:', err);
            if (commentsLoading) commentsLoading.style.display = 'none';
            if (commentsList) commentsList.innerHTML = '<div class="text-center py-4 text-muted small">Could not load comments.</div>';
        });
        }

        // Bind desktop side rail comment form submission
        const formEl = document.getElementById('fsRailCommentForm');
        if (formEl && !formEl.dataset.bound) {
            formEl.dataset.bound = 'true';
            formEl.onsubmit = function(e) {
                e.preventDefault();
                if (formEl.dataset.submitting === 'true') return;

                const curPostId = currentDesktopRailPostId;
                if (!curPostId) return;

                const content = commentInput?.value?.trim() || '';
                const attachmentType = formEl.querySelector('[name="attachment_type"]')?.value || '';
                const attachmentUrl = formEl.querySelector('[name="attachment_url"]')?.value || '';
                const attachmentMeta = formEl.querySelector('[name="attachment_meta"]')?.value || '';
                const photoInput = formEl.querySelector('.comment-photo-input') || formEl.querySelector('input[type="file"][name="attachment_image"]');
                const photoFile = (photoInput && photoInput.files && photoInput.files.length > 0) ? photoInput.files[0] : (formEl._pastedAttachmentFile || null);

                if (!content && !attachmentType && !photoFile) return;

                formEl.dataset.submitting = 'true';
                const sendBtn = formEl.querySelector('button[type="submit"]');
                if (sendBtn) sendBtn.disabled = true;

                const targetParentId = (parentInput && parentInput.value) ? parentInput.value : '';

                const csrfToken = getCsrfToken(formEl);
                const formData = new FormData(formEl);
                formData.set('content', content);
                formData.set('csrfmiddlewaretoken', csrfToken);
                if (targetParentId) {
                    formData.set('parent_id', targetParentId);
                }
                if (attachmentType) {
                    formData.set('attachment_type', attachmentType);
                    if (attachmentUrl) formData.set('attachment_url', attachmentUrl);
                    if (attachmentMeta) formData.set('attachment_meta', attachmentMeta);
                }
                if (photoFile) {
                    formData.set('attachment_image', photoFile);
                    if (!attachmentType) formData.set('attachment_type', 'image');
                }

                fetch(`/post/${curPostId}/comment/`, {
                    method: 'POST',
                    body: formData,
                    headers: {
                        'HX-Request': 'true',
                        'X-CSRFToken': csrfToken
                    }
                })
                .then(res => res.text())
                .then(html => {
                    delete formEl.dataset.submitting;
                    if (sendBtn) sendBtn.disabled = false;
                    if (commentInput) commentInput.value = '';
                    if (parentInput) parentInput.value = '';
                    if (replyBanner) replyBanner.classList.add('d-none');
                    if (window.MediaPicker) {
                        window.MediaPicker.clearAttachment(formEl);
                    }

                    if (commentsList) {
                        commentsList.innerHTML = html;
                        reinitHtmxElement(commentsList);
                        initSheetCommentsInteractions(commentsList, curPostId);
                    }

                    // Update all comment counters
                    let curN = parseInt(railCommentCount?.textContent || '0', 10) || 0;
                    let newN = curN + 1;
                    if (railCommentCount) railCommentCount.textContent = newN;
                    if (fsCommentCount) fsCommentCount.textContent = newN;

                    const inlinePostCount = document.getElementById(`comment-count-${curPostId}`);
                    const inlineReelCount = document.getElementById(`reel-comment-count-${curPostId}`);
                    const fsReelCount = document.getElementById(`fs-reel-comment-count-${curPostId}`);
                    if (inlinePostCount) inlinePostCount.textContent = newN;
                    if (inlineReelCount) inlineReelCount.textContent = newN;
                    if (fsReelCount) fsReelCount.textContent = newN;

                    if (targetParentId && commentsList) {
                        const parentEl = commentsList.querySelector(`#comment-${targetParentId}, [data-comment-id="${targetParentId}"]`);
                        const toggleBtn = parentEl?.querySelector('[data-action="toggle-replies"]');
                        if (toggleBtn) {
                            toggleBtn.click();
                            setTimeout(() => {
                                parentEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                            }, 250);
                        }
                    } else {
                        const scrollContainer = document.getElementById('fsRailCommentsBody');
                        if (scrollContainer) {
                            scrollContainer.scrollTo({ top: scrollContainer.scrollHeight, behavior: 'smooth' });
                        }
                    }
                })
                .catch(err => {
                    delete formEl.dataset.submitting;
                    if (sendBtn) sendBtn.disabled = false;
                    console.error('[DesktopSideRail] Comment post error:', err);
                });
            };
        }

        // Cancel reply in side rail
        const cancelReplyBtn = document.getElementById('fsRailCancelReply');
        if (cancelReplyBtn && !cancelReplyBtn.dataset.bound) {
            cancelReplyBtn.dataset.bound = 'true';
            cancelReplyBtn.onclick = function() {
                if (parentInput) parentInput.value = '';
                if (replyBanner) replyBanner.classList.add('d-none');
                if (commentInput && /^@\w+\s*$/.test(commentInput.value)) {
                    commentInput.value = '';
                    commentInput.placeholder = 'Add a comment...';
                }
            };
        }
    }

    function openFullscreenReels(targetPostId, sharedBy = null) {
        const overlay = document.getElementById('fullscreenReelsOverlay');
        const viewport = document.getElementById('reelsSnapViewport');
        if (!overlay || !viewport) return;

        targetPostId = targetPostId ? String(targetPostId).trim() : null;
        if (targetPostId === 'null' || targetPostId === 'undefined' || targetPostId === '') {
            targetPostId = null;
        }

        // Pause feed playback, suspend feed decoders, and save scroll offset
        state.fullscreenReturnFocus = document.activeElement;
        state.previousScrollY = window.scrollY || window.pageYOffset || document.documentElement.scrollTop || 0;
        pauseAllVideos();
        suspendFeedVideos();

        state.isFullScreenActive = true;
        document.body.classList.add('reels-active');
        overlay.classList.remove('d-none');
        overlay.setAttribute('aria-hidden', 'false');

        // Start desktop immersion helpers (idle doze + one-time rail tip).
        // Teardown of any previous session first — safe against double-open.
        if (typeof state.reelDesktopFxTeardown === 'function') {
            try { state.reelDesktopFxTeardown(); } catch (_) {}
        }
        state.reelDesktopFxTeardown = setupReelDesktopFx();
        // First desktop run: teach the keyboard (one-time, localStorage-gated)
        setTimeout(maybeShowReelKbCoach, 900);

        // Force white icons in status bar for fullscreen reels mode
        try {
            if (typeof window.updateStatusBarForTheme === 'function') {
                window.updateStatusBarForTheme('dark');
            } else {
                const bridge = window.AndroidBridge || window.vastoBridge;
                if (bridge && typeof bridge.setSystemBarTheme === 'function') {
                    bridge.setSystemBarTheme('dark');
                }
            }
        } catch (_) {}

        // Check and display subtle shared-by note if arriving via shared reel link
        if (!sharedBy) {
            try {
                const urlParams = new URLSearchParams(window.location.search);
                sharedBy = urlParams.get('shared_by');
            } catch (e) {}
        }
        if (sharedBy) {
            showSharedByNote(sharedBy);
        }

        // Clear previous snap tracks
        viewport.innerHTML = '';

        // Query all video cards on the page: vertical reels, carousel cards, and landscape video postcards
        const allVideoCards = [];
        const seenPostIds = new Set();

        const cardSelectors = [
            '.reel-card-container',
            '.reel-post-card',
            '.reels-carousel-card',
            '.post-card',
            '.landscape-video-container'
        ];

        document.querySelectorAll(cardSelectors.join(', ')).forEach(card => {
            const vid = card.querySelector('video') || (card.tagName === 'VIDEO' ? card : null);
            if (!vid || card.classList.contains('reels-carousel-shelf')) return;

            const cardPostId = String(extractPostId(card) || vid.dataset.postId || card.dataset.postId || '').trim();
            if (!cardPostId || cardPostId === 'null' || cardPostId === 'undefined' || seenPostIds.has(cardPostId)) return;

            seenPostIds.add(cardPostId);
            allVideoCards.push(card);
        });

        if (allVideoCards.length === 0) {
            console.warn('[VideoManager] No video cards found to populate fullscreen viewer');
            closeFullscreenReels();
            return;
        }

        createFullscreenObserver();

        let targetSnapItem = null;
        let originCurrentTime = 0;
        if (targetPostId) {
            const originCard = allVideoCards.find(c => {
                const pid = extractPostId(c) || c.querySelector('video')?.dataset.postId || c.dataset.postId;
                return pid && String(pid).trim() === targetPostId;
            });
            const originVid = originCard ? originCard.querySelector('video') : null;
            if (originVid && originVid.currentTime > 0) {
                originCurrentTime = originVid.currentTime;
            }
        }

        allVideoCards.forEach(card => {
            const snapItem = buildSnapItemFromReelCard(card);
            if (snapItem) {
                viewport.appendChild(snapItem);
                reinitHtmxElement(snapItem);

                const video = snapItem.querySelector('video');
                if (video) {
                    video.muted = isGlobalMuted;
                }

                if (targetPostId && String(snapItem.dataset.postId).trim() === targetPostId) {
                    targetSnapItem = snapItem;
                }
            }
        });

        // Initialize nav button visibility immediately
        updateFullscreenNavButtons(targetSnapItem || viewport.firstElementChild);

        // Request animation frame ensures DOM reflow is complete before scrolling/playing and observing
        requestAnimationFrame(() => {
            const activeItem = targetSnapItem || viewport.firstElementChild;
            if (activeItem) {
                const items = Array.from(viewport.children);
                const targetIndex = items.indexOf(activeItem);
                const validIndex = targetIndex > 0 ? targetIndex : 0;
                if (validIndex > 0) {
                    viewport.scrollTop = validIndex * viewport.clientHeight;
                } else {
                    viewport.scrollTop = 0;
                }

                // Activate 3-slot window: N plays, N-1 and N+1 warmed, rest evicted
                updateThreeSlotWindow(validIndex, originCurrentTime);

                setTimeout(() => {
                    adaptSnapItemOrientation(activeItem);
                }, 60);
            } else {
                updateFullscreenNavButtons();
            }

            // Observe snap items AFTER scrollTop is set so observer does not falsely trigger on item 0 first
            if (state.fullscreenObserver) {
                Array.from(viewport.children).forEach(item => {
                    state.fullscreenObserver.observe(item);
                });
            }
        });

        console.log('[VideoManager] Fullscreen reels overlay opened at post:', targetPostId);
    }

    function closeFullscreenReels() {
        const overlay = document.getElementById('fullscreenReelsOverlay');
        if (!overlay || overlay.classList.contains('d-none') || state.isClosingFullscreen) return;

        state.isClosingFullscreen = true;
        overlay.setAttribute('aria-busy', 'true');
        const closeBtn = document.getElementById('closeReelsOverlay');
        if (closeBtn) {
            closeBtn.disabled = true;
            closeBtn.classList.add('is-closing');
            closeBtn.setAttribute('aria-label', 'Closing reels');
            closeBtn.title = 'Closing reels…';
            closeBtn.querySelector('.close-reels-icon')?.classList.add('d-none');
            closeBtn.querySelector('.close-reels-spinner')?.classList.remove('d-none');
            closeBtn.querySelector('.close-reels-progress-label')?.classList.remove('d-none');
        }

        // Let the pending state paint before media and observer teardown begins.
        // Allow the disabled button and spinner to paint before decoder/DOM cleanup.
        // A single rAF still paints only after its callback returns, which made the
        // close button look frozen while the reel stack was being torn down.
        requestAnimationFrame(() => requestAnimationFrame(() => {
            try {
                finishClosingFullscreenReels();
            } finally {
                state.isClosingFullscreen = false;
                overlay.removeAttribute('aria-busy');
                const returnFocus = state.fullscreenReturnFocus;
                state.fullscreenReturnFocus = null;
                if (returnFocus && returnFocus.isConnected && typeof returnFocus.focus === 'function') {
                    try { returnFocus.focus({ preventScroll: true }); } catch (_) {}
                }
                if (closeBtn) {
                    closeBtn.disabled = false;
                    closeBtn.classList.remove('is-closing');
                    closeBtn.setAttribute('aria-label', 'Close Reels');
                    closeBtn.title = 'Back to feed';
                    closeBtn.querySelector('.close-reels-icon')?.classList.remove('d-none');
                    closeBtn.querySelector('.close-reels-spinner')?.classList.add('d-none');
                    closeBtn.querySelector('.close-reels-progress-label')?.classList.add('d-none');
                }
            }
        }));
    }

    function finishClosingFullscreenReels() {
        const overlay = document.getElementById('fullscreenReelsOverlay');
        const viewport = document.getElementById('reelsSnapViewport');
        if (!overlay) return;

        closeSideRailWebSocket();
        currentDesktopRailPostId = null;

        // Pause active fullscreen video
        pauseAllVideos();

        // Release every blurred-fill copy (decoders + network) on close.
        if (viewport) {
            Array.from(viewport.children).forEach(item => detachAmbientFill(item));
        }

        if (state.fullscreenObserver) {
            state.fullscreenObserver.disconnect();
        }

        if (viewport) {
            const snapItems = Array.from(viewport.children);
            const activeIdx = reelSlotEngine.activeIndex;
            // Cleanly evict media decoders only for active slot and its immediate warmed neighbors
            snapItems.forEach((item, idx) => {
                if (Math.abs(idx - activeIdx) <= 1) {
                    evictSnapItemMedia(item);
                }
            });
            viewport.innerHTML = '';
        }

        reelSlotEngine.activeIndex = -1;
        reelSlotEngine.previousIndex = -1;
        reelSlotEngine.nextIndex = -1;

        overlay.classList.add('d-none');
        overlay.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('reels-active');
        state.isFullScreenActive = false;

        // Stop desktop immersion helpers (idle doze timer + tip toast)
        if (typeof state.reelDesktopFxTeardown === 'function') {
            try { state.reelDesktopFxTeardown(); } catch (_) {}
            state.reelDesktopFxTeardown = null;
        }

        // Restore home feed scroll position immediately
        if (typeof state.previousScrollY === 'number') {
            try {
                window.scrollTo({ top: state.previousScrollY, behavior: 'instant' });
            } catch (_) {
                window.scrollTo(0, state.previousScrollY);
            }
        }

        // Restore feed videos and feed observer now that isFullScreenActive is false
        restoreFeedVideos();

        // Restore status bar theme to user's active theme
        try {
            const curTheme = document.documentElement.getAttribute('data-theme') || 'light';
            if (typeof window.updateStatusBarForTheme === 'function') {
                window.updateStatusBarForTheme(curTheme);
            } else {
                const bridge = window.AndroidBridge || window.vastoBridge;
                if (bridge && typeof bridge.setSystemBarTheme === 'function') {
                    bridge.setSystemBarTheme(curTheme);
                }
            }
        } catch (_) {}

        const noteEl = document.getElementById('fsReelSharedNote');
        if (noteEl) {
            noteEl.classList.add('d-none');
            noteEl.style.opacity = '0';
        }

        // Dismiss prompt (no count increment) and reset session counter
        _hideAutoScrollPrompt(false);
        _promptSessionReelCount = 0;
        _promptShownThisSession = false;

        // Reset comment rail state
        const sideRail = document.getElementById('reelDesktopSideRail');
        const playerLayout = document.querySelector('.reel-player-layout');
        const railToggleWrapper = document.getElementById('reelCommentRailToggleWrapper');
        if (sideRail) sideRail.classList.remove('rail-collapsed');
        if (playerLayout) playerLayout.classList.remove('rail-collapsed');
        if (railToggleWrapper) railToggleWrapper.classList.remove('is-visible');

        // Restore exact previous scroll position
        window.scrollTo({
            top: state.previousScrollY,
            behavior: 'instant'
        });

        console.log('[VideoManager] Fullscreen reels overlay closed. Restored scroll:', state.previousScrollY);
    }

    // ============================================================================
    // REEL QUICK TOOLS & COMMENT HELPERS
    // ============================================================================


    function openReelQuickTools(postId, targetEl) {
        if (!window.isAuthenticated) {
            if (typeof window.showGuestAuthPrompt === 'function') {
                window.showGuestAuthPrompt({
                    title: 'Sign in for more options',
                    subtitle: 'Sign in to vasto to save reels, repost, customize your feed, or report content.',
                    icon: 'bi-sliders'
                });
            }
            return;
        }

        postId = postId ? String(postId).trim() : '';
        if (!postId || postId === 'null' || postId === 'undefined') return;

        const modalEl = document.getElementById('reelQuickToolsModal');
        if (!modalEl || typeof bootstrap === 'undefined') return;

        // Resolve the card context: try closest ancestor, then active snap item, then feed card
        let card = targetEl
            ? targetEl.closest('.reel-card-container, .reel-post-card, .reels-snap-item')
            : null;

        if (!card) {
            // targetEl lives outside a snap/card (e.g. fsRailOptionsBtn in the desktop side rail)
            // — find the currently active snap item in the fullscreen viewport
            const viewport = document.getElementById('reelsSnapViewport');
            if (viewport && viewport.children.length > 0) {
                const h = viewport.clientHeight || 1;
                const curIdx = Math.round(viewport.scrollTop / h);
                card = viewport.children[curIdx] || viewport.firstElementChild || null;
            }
        }

        if (!card) {
            // Last resort: find the feed card or any element with this postId
            card = document.getElementById(`reel-card-${postId}`) ||
                   document.querySelector(`[data-post-id="${postId}"]`);
        }

        if (!card) return;

        const optionsBtn = card.querySelector('.reel-options-btn');
        const authorUsername = card.dataset.authorUsername ||
                               optionsBtn?.dataset.authorUsername ||
                               card.querySelector('.fb-author-username')?.textContent?.replace(/^@/, '')?.trim() ||
                               (optionsBtn?.dataset.author || '').replace(/^@/, '') ||
                               'Author';
        const isAuthor = optionsBtn?.dataset.isAuthor === 'true';
        const shareUrl = optionsBtn?.dataset.shareUrl || (window.location.origin + `/post/${postId}/`);
        const videoUrl = optionsBtn?.dataset.videoUrl || '';
        const posterUrl = optionsBtn?.dataset.poster || '';
        const caption = card.dataset.caption || card.querySelector('.reel-caption-text')?.textContent?.trim() || '';

        // Update Modal Header
        const authorEl = document.getElementById('quickToolsAuthor');
        if (authorEl) authorEl.textContent = `@${authorUsername}`;

        // 1. Bookmark / Save Button
        const bookmarkBtn = document.getElementById('quickToolsBookmarkBtn');
        if (bookmarkBtn) {
            const isBookmarked = window.pwaniSavedPosts ? window.pwaniSavedPosts.isPostSaved(postId) : bookmarkBtn.classList.contains('active');
            bookmarkBtn.classList.toggle('active', isBookmarked);
            const icon = bookmarkBtn.querySelector('i');
            const label = bookmarkBtn.querySelector('.fw-semibold');
            if (icon) icon.className = isBookmarked ? 'bi bi-bookmark-fill text-warning' : 'bi bi-bookmark';
            if (label) label.textContent = isBookmarked ? 'Saved to Library' : 'Save to Library';

            bookmarkBtn.onclick = function() {
                const nowSaved = window.pwaniSavedPosts ? window.pwaniSavedPosts.togglePostSaved(postId) : bookmarkBtn.classList.toggle('active');
                bookmarkBtn.classList.toggle('active', nowSaved);
                if (icon) icon.className = nowSaved ? 'bi bi-bookmark-fill text-warning' : 'bi bi-bookmark';
                if (label) label.textContent = nowSaved ? 'Saved to Library' : 'Save to Library';
                bootstrap.Modal.getInstance(modalEl)?.hide();
            };
        }

        // 2. Repost Button
        const repostBtn = document.getElementById('quickToolsRepostBtn');
        if (repostBtn) {
            repostBtn.onclick = function() {
                bootstrap.Modal.getInstance(modalEl)?.hide();
                const repostModalEl = document.getElementById('globalRepostModal');
                if (repostModalEl) {
                    const hiddenInput = repostModalEl.querySelector('input[name="post_id"]');
                    if (hiddenInput) hiddenInput.value = postId;
                    const form = document.getElementById('globalRepostForm');
                    if (form) form.setAttribute('hx-post', `/api/posts/${postId}/repost/`);
                    new bootstrap.Modal(repostModalEl).show();
                }
            };
        }

        // 3. Share Button
        const shareBtn = document.getElementById('quickToolsShareBtn');
        if (shareBtn) {
            shareBtn.onclick = function() {
                bootstrap.Modal.getInstance(modalEl)?.hide();
                const shareModalEl = document.getElementById('globalShareModal');
                if (shareModalEl) {
                    shareModalEl.setAttribute('data-post-id', postId);
                    shareModalEl.setAttribute('data-post-url', `/post/${postId}/`);
                    shareModalEl.setAttribute('data-video-url', videoUrl || '');
                    shareModalEl.setAttribute('data-poster-url', posterUrl || '');
                    shareModalEl.setAttribute('data-post-content', caption || '');
                    const hiddenPostId = document.getElementById('globalSharePostId');
                    if (hiddenPostId) hiddenPostId.value = postId;
                    const inst = bootstrap.Modal.getInstance(shareModalEl) || new bootstrap.Modal(shareModalEl);
                    inst.show();
                    if (window.pwaniPrepareShareModal) {
                        window.pwaniPrepareShareModal(postId, shareModalEl);
                    }
                }
            };
        }

        // 4. Copy Link Button
        const copyBtn = document.getElementById('quickToolsCopyLinkBtn');
        if (copyBtn) {
            copyBtn.onclick = function() {
                if (navigator.clipboard) {
                    navigator.clipboard.writeText(shareUrl).then(() => {
                        const label = copyBtn.querySelector('.fw-semibold');
                        if (label) label.textContent = 'Link Copied!';
                        setTimeout(() => {
                            if (label) label.textContent = 'Copy Link';
                            bootstrap.Modal.getInstance(modalEl)?.hide();
                        }, 800);
                    });
                }
            };
        }

        // 5. Download for Offline Button
        const downloadBtn = document.getElementById('quickToolsDownloadBtn');
        if (downloadBtn) {
            downloadBtn.onclick = async function() {
                bootstrap.Modal.getInstance(modalEl)?.hide();
                if (!videoUrl) {
                    if (typeof window.showFeedback === 'function') window.showFeedback('No video file available for download.', 'error');
                    return;
                }
                try {
                    const protectedDownload = card.querySelector('[data-action="download-media"][data-url]');
                    const downloadUrl = new URL(protectedDownload?.dataset.url || videoUrl, window.location.href).href;
                    if (!window.downloadManager || typeof window.downloadManager.download !== 'function') {
                        throw new Error('Download service is unavailable. Please reload and try again.');
                    }
                    await window.downloadManager.download({
                        url: downloadUrl,
                        postId,
                        filename: `reel_${postId}.mp4`,
                        mediaType: 'video',
                        category: 'video',
                        thumbnail: posterUrl
                    });
                    if (typeof window.showFeedback === 'function') window.showFeedback('Download started.', 'success');
                } catch (error) {
                    console.error('[Quick Tools Download] Failed:', error);
                    if (typeof window.showFeedback === 'function') window.showFeedback(error.message || 'Download failed.', 'error');
                }
            };
        }

        // 6. Hide Button
        const hideBtn = document.getElementById('quickToolsHideBtn');
        if (hideBtn) {
            hideBtn.onclick = function() {
                bootstrap.Modal.getInstance(modalEl)?.hide();
                if (card) {
                    card.style.transition = 'opacity 0.25s ease, transform 0.25s ease';
                    card.style.opacity = '0';
                    card.style.transform = 'scale(0.95)';
                    setTimeout(() => card.remove(), 250);
                }
            };
        }

        // 7. Report Button
        const reportBtn = document.getElementById('quickToolsReportBtn');
        if (reportBtn) {
            reportBtn.onclick = function() {
                bootstrap.Modal.getInstance(modalEl)?.hide();
                const reportModalEl = document.getElementById('globalReportModal');
                if (reportModalEl) {
                    const hiddenInput = reportModalEl.querySelector('input[name="post"]');
                    if (hiddenInput) hiddenInput.value = postId;
                    new bootstrap.Modal(reportModalEl).show();
                }
            };
        }

        // 8. Delete Button (Author only)
        const deleteBtn = document.getElementById('quickToolsDeleteBtn');
        if (deleteBtn) {
            if (isAuthor) {
                deleteBtn.classList.remove('d-none');
                deleteBtn.onclick = function() {
                    if (confirm('Are you sure you want to delete this reel?')) {
                        bootstrap.Modal.getInstance(modalEl)?.hide();
                        fetch(`/api/posts/${postId}/`, {
                            method: 'DELETE',
                            headers: {
                                'X-CSRFToken': getCsrfToken()
                            }
                        }).then(() => {
                            card.remove();
                        });
                    }
                };
            } else {
                deleteBtn.classList.add('d-none');
            }
        }

        // 9. Auto-Scroll Toggle Button
        const autoScrollBtn = document.getElementById('quickToolsAutoScrollBtn');
        if (autoScrollBtn) {
            // Sync initial state immediately
            syncAutoScrollUI();
            autoScrollBtn.onclick = function() {
                toggleAutoScroll();
                // Visual feedback toast
                const isNowOn = isAutoScrollEnabled;
                let toastEl = document.getElementById('autoScrollToast');
                if (!toastEl) {
                    toastEl = document.createElement('div');
                    toastEl.id = 'autoScrollToast';
                    toastEl.className = 'position-fixed bottom-0 start-50 translate-middle-x mb-5 px-3 py-2 rounded-pill shadow text-white text-center';
                    toastEl.style.cssText = 'z-index:10999;font-size:13px;pointer-events:none;transition:opacity 0.2s ease,transform 0.2s ease;';
                    document.body.appendChild(toastEl);
                }
                toastEl.style.background = isNowOn ? '#0d6efd' : '#6c757d';
                toastEl.textContent = isNowOn ? '▶ Auto-scroll On' : '⏸ Auto-scroll Off';
                toastEl.style.opacity = '1';
                toastEl.style.transform = 'translate(-50%, 0)';
                clearTimeout(toastEl._t);
                toastEl._t = setTimeout(() => {
                    toastEl.style.opacity = '0';
                    toastEl.style.transform = 'translate(-50%, 10px)';
                }, 1800);
            };
        }

        // 10. Video Quality Selector (Adaptive Bitrate Control)
        const qualitySelect = document.getElementById('quickToolsQualitySelect');
        const qualitySub = document.getElementById('quickToolsQualitySub');
        if (qualitySelect && window.PwaniVideoQualityManager) {
            const currentPref = window.PwaniVideoQualityManager.getPreferredQuality();
            qualitySelect.value = currentPref;

            const updateQualitySubLabel = () => {
                if (!qualitySub) return;
                const vid = card.querySelector('video') || document.querySelector(`.reels-snap-item[data-post-id="${postId}"] video`);
                const liveLabel = vid?.dataset?.currentQualityLabel;
                if (qualitySelect.value === 'auto') {
                    qualitySub.textContent = liveLabel ? `Auto (${liveLabel} streaming)` : 'Adaptive bitrate (Auto)';
                } else {
                    qualitySub.textContent = `Locked at ${qualitySelect.value}p`;
                }
            };
            updateQualitySubLabel();

            qualitySelect.onchange = function() {
                const newQual = qualitySelect.value;
                window.PwaniVideoQualityManager.setPreferredQuality(newQual);
                updateQualitySubLabel();

                let qToast = document.getElementById('reelQualityToast');
                if (!qToast) {
                    qToast = document.createElement('div');
                    qToast.id = 'reelQualityToast';
                    qToast.className = 'position-fixed bottom-0 start-50 translate-middle-x mb-5 px-3 py-2 rounded-pill shadow text-white text-center';
                    qToast.style.cssText = 'z-index:10999;font-size:13px;pointer-events:none;transition:opacity 0.2s ease,transform 0.2s ease;background:#0d6efd;';
                    document.body.appendChild(qToast);
                }
                qToast.textContent = newQual === 'auto' ? '⚡ Video Quality: Auto (Adaptive)' : `📺 Video Quality: ${newQual}p`;
                qToast.style.opacity = '1';
                qToast.style.transform = 'translate(-50%, 0)';
                clearTimeout(qToast._t);
                qToast._t = setTimeout(() => {
                    qToast.style.opacity = '0';
                    qToast.style.transform = 'translate(-50%, 10px)';
                }, 1800);
            };
        }

        // Sync auto-scroll UI state before showing
        syncAutoScrollUI();
        new bootstrap.Modal(modalEl).show();
    }

    let activeCommentsPostId = null;

    function closeAllSheetCommentMenus() {
        document.querySelectorAll('.comment-menu-dropdown').forEach(m => m.remove());
        document.querySelectorAll('.comment-item.menu-open').forEach(el => el.classList.remove('menu-open'));
        document.querySelectorAll('[data-action="menu"]').forEach(b => b.setAttribute('aria-expanded', 'false'));
    }

    function showCommentsToast(message) {
        let toast = document.getElementById('vastoCommentsToast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'vastoCommentsToast';
            toast.className = 'position-fixed bottom-0 start-50 translate-middle-x mb-4 px-3 py-2 rounded-pill shadow bg-dark text-white text-center';
            toast.style.zIndex = '10950';
            toast.style.fontSize = '13px';
            toast.style.pointerEvents = 'none';
            toast.style.transition = 'opacity 0.2s ease, transform 0.2s ease';
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.style.opacity = '1';
        toast.style.transform = 'translate(-50%, 0)';
        clearTimeout(toast._timeout);
        toast._timeout = setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translate(-50%, 10px)';
        }, 2200);
    }

    if (!window._commentMenuListenersBound) {
        window._commentMenuListenersBound = true;
        document.addEventListener('click', function(e) {
            if (!e.target.closest('.comment-menu-dropdown') && !e.target.closest('[data-action="menu"]')) {
                closeAllSheetCommentMenus();
            }
        });
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                closeAllSheetCommentMenus();
            }
        });
    }

    function truncateLongComments(container) {
        if (!container) return;
        const bodies = container.querySelectorAll('.comment-body');
        bodies.forEach(body => {
            if (body.dataset.clampHandled === 'true') return;
            body.dataset.clampHandled = 'true';

            const text = body.textContent.trim();
            if (text.length > 130 || (text.match(/\n/g) || []).length >= 3) {
                body.classList.add('comment-body-clamped');
                const toggleBtn = document.createElement('button');
                toggleBtn.type = 'button';
                toggleBtn.className = 'btn btn-link btn-sm p-0 text-muted text-decoration-none fw-semibold comment-see-more-btn';
                toggleBtn.style.fontSize = '12px';
                toggleBtn.style.display = 'inline-block';
                toggleBtn.style.marginTop = '2px';
                toggleBtn.textContent = '... See more';
                toggleBtn.onclick = function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    const isExp = body.classList.toggle('is-expanded');
                    body.classList.toggle('comment-body-clamped', !isExp);
                    toggleBtn.textContent = isExp ? 'See less' : '... See more';
                };
                body.after(toggleBtn);
            }
        });
    }

    function setupCommentsInfiniteScroll(containerEl) {
        if (!containerEl) return;
        const sentinel = containerEl.querySelector('.comments-load-more-sentinel');
        if (!sentinel || sentinel.dataset.observed === 'true') return;
        sentinel.dataset.observed = 'true';

        const loadNextChunk = async () => {
            if (sentinel.dataset.loading === 'true') return;
            const nextPage = sentinel.dataset.nextPage;
            const targetPostId = sentinel.dataset.postId || containerEl.dataset.activePostId || activeCommentsPostId || currentDesktopRailPostId || '';
            if (!nextPage || !targetPostId) return;

            sentinel.dataset.loading = 'true';
            const spinner = sentinel.querySelector('.comments-loading-spinner');
            const btn = sentinel.querySelector('.comments-load-more-btn');
            if (spinner) spinner.classList.remove('d-none');
            if (btn) btn.classList.add('d-none');

            try {
                const res = await fetch(`/post/${targetPostId}/?show_all=1&page=${nextPage}&chunk_only=1`, {
                    headers: { 'HX-Request': 'true' }
                });
                if (!res.ok) throw new Error('Failed to load more comments');
                const chunkHtml = await res.text();

                // Create a temporary container to parse items
                const temp = document.createElement('div');
                temp.innerHTML = chunkHtml;

                const newComments = Array.from(temp.querySelectorAll('.comment-item'));
                const newSentinel = temp.querySelector('.comments-load-more-sentinel');

                // Insert new comments before the sentinel
                newComments.forEach(c => {
                    sentinel.parentNode.insertBefore(c, sentinel);
                });

                if (newSentinel) {
                    sentinel.dataset.nextPage = newSentinel.dataset.nextPage;
                    delete sentinel.dataset.loading;
                    delete sentinel.dataset.observed;
                    if (spinner) spinner.classList.add('d-none');
                    if (btn) {
                        btn.classList.remove('d-none');
                        btn.textContent = 'Load more comments...';
                    }
                    setupCommentsInfiniteScroll(containerEl);
                } else {
                    sentinel.remove();
                }

                truncateLongComments(containerEl);
                reinitHtmxElement(containerEl);
            } catch (err) {
                console.error('[CommentsSheet] Error loading comment chunk:', err);
                delete sentinel.dataset.loading;
                if (spinner) spinner.classList.add('d-none');
                if (btn) {
                    btn.classList.remove('d-none');
                    btn.textContent = 'Retry loading comments...';
                }
            }
        };

        // Click fallback on "Load more comments..." button
        const btn = sentinel.querySelector('.comments-load-more-btn');
        if (btn) {
            btn.onclick = (e) => {
                e.preventDefault();
                loadNextChunk();
            };
        }

        // Automatic scroll loading via IntersectionObserver
        if ('IntersectionObserver' in window) {
            const scrollRoot = containerEl.closest('#commentsModalBody, #fsRailCommentsBody, .modal-body');
            const observer = new IntersectionObserver((entries) => {
                if (entries[0] && entries[0].isIntersecting) {
                    loadNextChunk();
                }
            }, {
                root: scrollRoot || null,
                rootMargin: '180px'
            });
            observer.observe(sentinel);
        }
    }

    function initSheetCommentsInteractions(listEl, postId) {
        if (!listEl) return;
        if (postId) {
            listEl.dataset.activePostId = String(postId).trim();
        }
        truncateLongComments(listEl);
        reinitHtmxElement(listEl);
        setupCommentsInfiniteScroll(listEl);
        if (listEl.dataset.eventsBound === 'true') return;
        listEl.dataset.eventsBound = 'true';

        listEl.addEventListener('click', async function(e) {
            const curPostId = listEl.dataset.activePostId || currentDesktopRailPostId || activeCommentsPostId || postId || '';

            const findCommentEl = (commentId, triggerEl) => {
                if (triggerEl) {
                    const closest = triggerEl.closest('.comment-item');
                    if (closest) return closest;
                }
                return listEl.querySelector(`#comment-${commentId}, [data-comment-id="${commentId}"]`) ||
                       document.getElementById(`comment-${commentId}`);
            };

            const findBodyEl = (commentId, commentEl) => {
                return commentEl?.querySelector(`#comment-body-${commentId}, .comment-body`) ||
                       listEl.querySelector(`#comment-body-${commentId}`) ||
                       document.getElementById(`comment-body-${commentId}`);
            };

            // A. Comment Like
            const likeBtn = e.target.closest('[data-action="like"]');
            if (likeBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                const commentId = likeBtn.dataset.commentId;
                if (!commentId) return;

                const icon = likeBtn.querySelector('i');
                const countSpan = likeBtn.querySelector('span');
                const wasLiked = likeBtn.classList.contains('liked');
                const curCount = parseInt(countSpan?.textContent || '0', 10) || 0;
                const optimisticLiked = !wasLiked;
                const optimisticCount = wasLiked ? Math.max(0, curCount - 1) : curCount + 1;

                const applyCommentLikeUi = (likedState, countVal) => {
                    document.querySelectorAll(`[data-action="like"][data-comment-id="${commentId}"]`).forEach(btn => {
                        btn.classList.toggle('liked', likedState);
                        btn.classList.toggle('text-danger', likedState);
                        const ic = btn.querySelector('i');
                        if (ic) {
                            ic.className = likedState ? 'bi bi-heart-fill text-danger' : 'bi bi-heart';
                        }
                        const sp = btn.querySelector('span');
                        if (sp) {
                            sp.textContent = countVal;
                        }
                    });
                };

                // Optimistic UI toggle across all instances of this comment
                applyCommentLikeUi(optimisticLiked, optimisticCount);

                try {
                    const res = await fetch(`/api/comments/${commentId}/like/`, {
                        method: 'POST',
                        headers: {
                            'X-CSRFToken': getCsrfToken(),
                            'Content-Type': 'application/json'
                        }
                    });
                    if (res.ok) {
                        const data = await res.json().catch(() => null);
                        if (data) {
                            const serverLiked = data.detail === 'Comment liked.'
                                ? true
                                : (data.detail === 'Comment unliked.' ? false : optimisticLiked);
                            const serverCount = (typeof data.likes_count === 'number') ? data.likes_count : optimisticCount;
                            applyCommentLikeUi(serverLiked, serverCount);
                        }
                    } else {
                        // Do NOT fire a second toggle request — the first may
                        // have still succeeded server-side, in which case a
                        // retry toggles the like back off. Restore the UI
                        // instead; the next render/refresh shows server truth.
                        console.warn('[CommentsSheet] Like toggle rejected:', res.status);
                        applyCommentLikeUi(wasLiked, curCount);
                    }
                } catch (err) {
                    console.warn('[CommentsSheet] Like toggle error:', err);
                    applyCommentLikeUi(wasLiked, curCount);
                }
                return;
            }

            // B. Comment Reply
            const replyBtn = e.target.closest('[data-action="reply"]');
            if (replyBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                const commentId = replyBtn.dataset.commentId;
                const commentItem = findCommentEl(commentId, replyBtn);
                // Enforce 1-level maximum nesting: if replying to a reply, anchor to its root parent comment
                const rootParentId = commentItem?.dataset?.parentId || commentItem?.dataset?.parentCommentId || commentId;
                const authorLink = commentItem?.querySelector('.comment-author-link');
                const authorUsername = commentItem?.dataset?.authorUsername ||
                                       authorLink?.dataset?.username ||
                                       (authorLink?.getAttribute('href') || '').match(/\/users\/(?:user\/)?([^\/]+)/)?.[1] ||
                                       authorLink?.textContent?.trim() || '';

                const isRail = (listEl && listEl.id === 'fsRailCommentsList') ||
                               (listEl && Boolean(listEl.closest('#fsRailCommentsBody, .reel-desktop-side-rail'))) ||
                               Boolean(document.getElementById('fsRailCommentInput')?.offsetParent);

                if (isRail) {
                    toggleDesktopCommentRail(true);
                }

                const parentInput = isRail ? document.getElementById('fsRailParentId') : document.getElementById('commentsModalParentId');
                const replyBanner = isRail ? document.getElementById('fsRailReplyBanner') : document.getElementById('commentsModalReplyBanner');
                const replyUserSpan = isRail ? document.getElementById('fsRailReplyUser') : document.getElementById('commentsModalReplyUser');
                const inputEl = isRail ? document.getElementById('fsRailCommentInput') : document.getElementById('reelCommentInput');

                if (parentInput) parentInput.value = rootParentId;
                if (replyBanner && replyUserSpan) {
                    replyUserSpan.textContent = authorUsername ? `@${authorUsername}` : 'comment';
                    replyBanner.classList.remove('d-none');
                }
                if (inputEl) {
                    if (authorUsername && !inputEl.value.includes(`@${authorUsername}`)) {
                        inputEl.value = `@${authorUsername} `;
                    }
                    inputEl.focus();
                }
                return;
            }

            // C. Toggle Replies
            const toggleBtn = e.target.closest('[data-action="toggle-replies"]');
            if (toggleBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                if (toggleBtn.dataset.loading === 'true') return;
                const commentId = toggleBtn.dataset.commentId;
                const parentComment = findCommentEl(commentId, toggleBtn);
                if (!parentComment) return;

                let repliesContainer = parentComment.querySelector('.replies-container') ||
                                       parentComment.querySelector(`#replies-${commentId}`) ||
                                       listEl.querySelector(`#replies-${commentId}`);
                const isExpanded = toggleBtn.classList.contains('expanded');

                if (isExpanded) {
                    // Collapse
                    if (repliesContainer) {
                        repliesContainer.classList.remove('expanded');
                        repliesContainer.classList.add('collapsed');
                        repliesContainer.style.display = 'none';
                    }
                    toggleBtn.classList.remove('expanded');
                    const icon = toggleBtn.querySelector('i');
                    if (icon) {
                        icon.classList.remove('bi-chevron-up');
                        icon.classList.add('bi-chevron-down');
                    }
                    const span = toggleBtn.querySelector('span');
                    if (span) span.textContent = `View ${toggleBtn.dataset.replyCount || ''} replies`;
                } else {
                    // Expand
                    if (repliesContainer && repliesContainer.children.length > 0) {
                        repliesContainer.classList.remove('collapsed');
                        repliesContainer.classList.add('expanded');
                        repliesContainer.style.display = 'block';
                    } else {
                        // Fetch replies from API
                        toggleBtn.dataset.loading = 'true';
                        try {
                            const res = await fetch(`/api/comments/${commentId}/replies/?page=1&page_size=20`);
                            if (res.ok) {
                                const data = await res.json();
                                if (!repliesContainer) {
                                    repliesContainer = document.createElement('div');
                                    repliesContainer.id = `replies-${commentId}`;
                                    repliesContainer.className = 'replies-container expanded ms-4 mt-2 ps-2 border-start';
                                    const contentDiv = parentComment.querySelector('.comment-content') || parentComment;
                                    contentDiv.appendChild(repliesContainer);
                                }
                                repliesContainer.innerHTML = '';
                                const results = data.results || (Array.isArray(data) ? data : []);
                                const seenIds = new Set();
                                results.forEach(reply => {
                                    if (seenIds.has(reply.id)) return;
                                    seenIds.add(reply.id);
                                    const replyAvatar = reply.author?.profile_pic || '/static/images/default-avatar.png';
                                    const replyAuthor = reply.author?.full_name || reply.author?.username || 'User';
                                    const replyUsername = reply.author?.username || '';
                                    const replyLiked = reply.is_liked ? 'liked text-danger' : '';
                                    const profilePath = `/users/user/${replyUsername}/`;
                                    let replyAttachmentHtml = '';
                                    const replyAttUrl = reply.attachment_url || reply.attachment_image || '';
                                    if (reply.attachment_type && reply.attachment_type !== 'none' && replyAttUrl) {
                                        if (reply.attachment_type === 'gif') {
                                            replyAttachmentHtml = `<div class="comment-rendered-attachment my-1"><img src="${replyAttUrl}" class="comment-gif-image" alt="GIF" loading="lazy"></div>`;
                                        } else {
                                            replyAttachmentHtml = `<div class="comment-rendered-attachment my-1"><img src="${replyAttUrl}" class="comment-sticker-image" alt="Sticker" loading="lazy" onclick="if(window.openFullscreenImage) window.openFullscreenImage(this.src);"></div>`;
                                        }
                                    }

                                    const replyHtml = `
                                        <div class="comment-item reply-item py-2 border-bottom border-light" id="comment-${reply.id}" data-comment-id="${reply.id}" data-parent-id="${commentId}" data-author-id="${reply.author?.id || ''}" data-author-username="${replyUsername}">
                                            <div class="d-flex align-items-start gap-2">
                                                <a href="${profilePath}"
                                                   hx-get="${profilePath}"
                                                   hx-target="#page-content-target"
                                                   hx-swap="innerHTML"
                                                   hx-push-url="true"
                                                   class="text-decoration-none comment-avatar-link">
                                                    <img src="${replyAvatar}" class="rounded-circle border" style="width: 28px; height: 28px; object-fit: cover;" alt="${replyAuthor}">
                                                </a>
                                                <div class="flex-grow-1 min-w-0">
                                                    <div class="d-flex align-items-center justify-content-between">
                                                        <a href="${profilePath}"
                                                           hx-get="${profilePath}"
                                                           hx-target="#page-content-target"
                                                           hx-swap="innerHTML"
                                                           hx-push-url="true"
                                                           class="fw-bold text-dark text-decoration-none small comment-author-link" data-username="${replyUsername}">${replyAuthor}</a>
                                                        <span class="text-muted" style="font-size: 11px;">${reply.created_at ? new Date(reply.created_at).toLocaleDateString() : ''}</span>
                                                    </div>
                                                    <div class="small text-break mt-0.5 comment-body" id="comment-body-${reply.id}">${reply.content}</div>
                                                    ${replyAttachmentHtml}
                                                    <div class="comment-actions d-flex align-items-center gap-3 mt-1" style="font-size: 11px;">
                                                        <button type="button" class="btn btn-link p-0 text-muted text-decoration-none comment-action ${replyLiked}" data-action="like" data-comment-id="${reply.id}">
                                                            <i class="bi ${reply.is_liked ? 'bi-heart-fill text-danger' : 'bi-heart'}"></i>
                                                            <span class="ms-0.5">${reply.like_count || 0}</span>
                                                        </button>
                                                        <button type="button" class="btn btn-link p-0 text-muted text-decoration-none comment-action" data-action="reply" data-comment-id="${reply.id}">
                                                            <i class="bi bi-chat-dots"></i> Reply
                                                        </button>
                                                        <div class="comment-menu-wrapper position-relative d-inline-flex">
                                                            <button type="button" class="btn btn-link p-0 text-muted comment-menu-btn" data-action="menu" data-comment-id="${reply.id}" aria-label="More options" aria-expanded="false">
                                                                <i class="bi bi-three-dots"></i>
                                                            </button>
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    `;
                                    repliesContainer.insertAdjacentHTML('beforeend', replyHtml);
                                });
                                reinitHtmxElement(repliesContainer);
                                truncateLongComments(repliesContainer);
                                repliesContainer.style.display = 'block';
                            }
                        } catch (err) {
                            console.warn('[CommentsSheet] Failed to load replies:', err);
                        } finally {
                            toggleBtn.dataset.loading = 'false';
                        }
                    }
                    toggleBtn.classList.add('expanded');
                    const icon = toggleBtn.querySelector('i');
                    if (icon) {
                        icon.classList.remove('bi-chevron-down');
                        icon.classList.add('bi-chevron-up');
                    }
                    const span = toggleBtn.querySelector('span');
                    if (span) span.textContent = 'Hide replies';
                }
                return;
            }

            // D. Comment Context Menu (Three Dots)
            const menuBtn = e.target.closest('[data-action="menu"]');
            if (menuBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                if (!window.isAuthenticated) {
                    if (typeof window.showGuestAuthPrompt === 'function') {
                        window.showGuestAuthPrompt({
                            title: 'Sign in for comment options',
                            subtitle: 'Sign in to vasto to interact with comments, view profiles, and join the discussion.',
                            icon: 'bi-chat-dots-fill'
                        });
                    }
                    return;
                }
                const commentId = menuBtn.dataset.commentId;
                if (!commentId) return;

                const commentEl = findCommentEl(commentId, menuBtn);
                if (!commentEl) return;

                const existingMenu = commentEl.querySelector('.comment-menu-dropdown') ||
                                     listEl.querySelector(`#menu-${commentId}`);
                closeAllSheetCommentMenus();
                if (existingMenu) return;

                const currentUserId = window.vastoUserId || document.body.dataset.userId;
                const currentUsername = window.vastoUsername || document.body.dataset.userUsername;
                const currentUserRole = window.vastoUserRole || document.body.dataset.userRole;

                const authorId = commentEl.dataset.authorId;
                const authorLink = commentEl.querySelector('.comment-author-link');
                const authorUsername = commentEl.dataset.authorUsername ||
                                       authorLink?.dataset?.username ||
                                       (authorLink?.getAttribute('href') || '').match(/\/users\/(?:user\/)?([^\/]+)/)?.[1] ||
                                       '';

                const isOwner = (currentUserId && authorId && String(currentUserId) === String(authorId)) ||
                                (currentUsername && authorUsername && currentUsername.toLowerCase() === authorUsername.toLowerCase());
                const isModerator = ['admin', 'moderator', 'president', 'delegate'].includes((currentUserRole || '').toLowerCase());

                let menuHtml = '';
                if (typeof CommentRenderer !== 'undefined' && CommentRenderer.renderMenu) {
                    menuHtml = CommentRenderer.renderMenu(commentId, isOwner, isModerator);
                } else {
                    let items = '';
                    if (isOwner) {
                        items += `
                            <button type="button" class="comment-menu-item" data-action="edit" data-comment-id="${commentId}">
                                <i class="bi bi-pencil"></i> Edit
                            </button>
                            <button type="button" class="comment-menu-item danger" data-action="delete" data-comment-id="${commentId}">
                                <i class="bi bi-trash"></i> Delete
                            </button>
                            <div class="comment-menu-divider"></div>
                        `;
                    } else if (isModerator) {
                        items += `
                            <button type="button" class="comment-menu-item danger" data-action="delete" data-comment-id="${commentId}">
                                <i class="bi bi-trash"></i> Delete
                            </button>
                            <div class="comment-menu-divider"></div>
                        `;
                    }
                    items += `
                        <button type="button" class="comment-menu-item" data-action="copy-text" data-comment-id="${commentId}">
                            <i class="bi bi-copy"></i> Copy text
                        </button>
                        <button type="button" class="comment-menu-item" data-action="copy-link" data-comment-id="${commentId}">
                            <i class="bi bi-link-45deg"></i> Copy link
                        </button>
                        <button type="button" class="comment-menu-item" data-action="view-profile" data-comment-id="${commentId}">
                            <i class="bi bi-person"></i> View profile
                        </button>
                    `;
                    menuHtml = `<div class="comment-menu-dropdown" id="menu-${commentId}" role="menu">${items}</div>`;
                }

                const wrapper = menuBtn.closest('.comment-menu-wrapper') || menuBtn.parentElement;
                commentEl.classList.add('menu-open');
                wrapper.insertAdjacentHTML('beforeend', menuHtml);
                menuBtn.setAttribute('aria-expanded', 'true');

                const menuEl = wrapper.querySelector('.comment-menu-dropdown') || listEl.querySelector(`#menu-${commentId}`);
                if (menuEl) {
                    const rect = menuEl.getBoundingClientRect();
                    const scrollContainer = menuBtn.closest('#fsRailCommentsBody, #commentsModalBody, .modal-body');
                    const containerRect = (scrollContainer && scrollContainer.offsetParent !== null)
                        ? scrollContainer.getBoundingClientRect()
                        : { top: 0, bottom: window.innerHeight, left: 0, right: window.innerWidth };

                    if (rect.bottom > containerRect.bottom - 16 && (rect.top - rect.height) > containerRect.top) {
                        menuEl.classList.add('menu-dropup');
                    }
                    if (rect.left < containerRect.left + 8) {
                        menuEl.style.left = '0';
                        menuEl.style.right = 'auto';
                    }
                }
                return;
            }

            // E. Copy Comment Text
            const copyTextBtn = e.target.closest('[data-action="copy-text"]');
            if (copyTextBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                const commentId = copyTextBtn.dataset.commentId;
                const commentEl = findCommentEl(commentId, copyTextBtn);
                closeAllSheetCommentMenus();
                const bodyEl = findBodyEl(commentId, commentEl);
                const text = bodyEl ? bodyEl.textContent.trim() : '';
                if (navigator.clipboard && text) {
                    navigator.clipboard.writeText(text).then(() => {
                        showCommentsToast('Comment text copied to clipboard');
                    }).catch(() => {
                        showCommentsToast('Failed to copy text');
                    });
                }
                return;
            }

            // F. Copy Comment Link
            const copyLinkBtn = e.target.closest('[data-action="copy-link"]');
            if (copyLinkBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                const commentId = copyLinkBtn.dataset.commentId;
                closeAllSheetCommentMenus();
                const url = `${window.location.origin}/post/${curPostId}/#comment-${commentId}`;
                if (navigator.clipboard) {
                    navigator.clipboard.writeText(url).then(() => {
                        showCommentsToast('Comment link copied to clipboard');
                    }).catch(() => {
                        showCommentsToast('Failed to copy link');
                    });
                }
                return;
            }

            // G. View Profile
            const viewProfileBtn = e.target.closest('[data-action="view-profile"]');
            if (viewProfileBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                const commentId = viewProfileBtn.dataset.commentId;
                const commentEl = findCommentEl(commentId, viewProfileBtn);
                closeAllSheetCommentMenus();
                const profileLink = commentEl?.querySelector('.comment-author-link') || commentEl?.querySelector('.comment-avatar-link') || commentEl?.querySelector('a');
                const targetHref = profileLink?.getAttribute('hx-get') || profileLink?.getAttribute('href') || profileLink?.href;
                if (targetHref) {
                    if (state.isFullScreenActive) {
                        closeFullscreenReels();
                    }
                    const modalEl = document.getElementById('commentsModal');
                    if (modalEl && typeof bootstrap !== 'undefined') {
                        try { bootstrap.Modal.getInstance(modalEl)?.hide(); } catch (_) {}
                    }
                    if (window.htmx && document.getElementById('page-content-target')) {
                        window.htmx.ajax('GET', targetHref, {
                            target: '#page-content-target',
                            swap: 'innerHTML',
                            headers: {
                                'HX-Request': 'true',
                                'HX-Target': 'page-content-target'
                            }
                        });
                        try { window.history.pushState({}, '', targetHref); } catch (_) {}
                    } else {
                        window.location.href = targetHref;
                    }
                }
                return;
            }

            // H. Edit Comment Mode
            const editBtn = e.target.closest('[data-action="edit"]');
            if (editBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                const commentId = editBtn.dataset.commentId;
                const commentEl = findCommentEl(commentId, editBtn);
                closeAllSheetCommentMenus();
                const bodyEl = findBodyEl(commentId, commentEl);
                if (!bodyEl) return;
                if (commentEl?.querySelector(`#edit-mode-${commentId}`)) return;

                const fullContentSpan = bodyEl.querySelector('.comment-content-full');
                const curText = (fullContentSpan ? fullContentSpan.textContent : bodyEl.textContent).trim();
                bodyEl.dataset.originalText = curText;
                bodyEl.style.display = 'none';

                const editHtml = `
                    <div class="comment-edit-mode mt-1" id="edit-mode-${commentId}">
                        <textarea class="form-control form-control-sm mb-1 edit-textarea" id="edit-input-${commentId}" rows="2" style="font-size: 13px;">${curText}</textarea>
                        <div class="d-flex justify-content-end gap-2">
                            <button type="button" class="btn btn-sm btn-light border py-0 px-2" data-action="cancel-edit" data-comment-id="${commentId}" style="font-size: 12px;">Cancel</button>
                            <button type="button" class="btn btn-sm btn-primary py-0 px-2" data-action="save-edit" data-comment-id="${commentId}" style="font-size: 12px;">Save</button>
                        </div>
                    </div>
                `;
                bodyEl.insertAdjacentHTML('afterend', editHtml);
                const textarea = commentEl?.querySelector(`#edit-input-${commentId}`) || document.getElementById(`edit-input-${commentId}`);
                if (textarea) textarea.focus();
                return;
            }

            // I. Cancel Edit Mode
            const cancelEditBtn = e.target.closest('[data-action="cancel-edit"]');
            if (cancelEditBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                const commentId = cancelEditBtn.dataset.commentId;
                const commentEl = findCommentEl(commentId, cancelEditBtn);
                const editMode = commentEl?.querySelector(`#edit-mode-${commentId}`) || document.getElementById(`edit-mode-${commentId}`);
                const bodyEl = findBodyEl(commentId, commentEl);
                if (bodyEl) bodyEl.style.display = '';
                if (editMode) editMode.remove();
                return;
            }

            // J. Save Edit
            const saveEditBtn = e.target.closest('[data-action="save-edit"]');
            if (saveEditBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                const commentId = saveEditBtn.dataset.commentId;
                const commentEl = findCommentEl(commentId, saveEditBtn);
                const textarea = commentEl?.querySelector(`#edit-input-${commentId}`) || document.getElementById(`edit-input-${commentId}`);
                const newText = textarea ? textarea.value.trim() : '';
                if (!newText) return;

                saveEditBtn.disabled = true;
                saveEditBtn.textContent = 'Saving...';

                try {
                    const res = await fetch(`/api/comments/${commentId}/`, {
                        method: 'PATCH',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': getCsrfToken()
                        },
                        body: JSON.stringify({ content: newText })
                    });
                    if (res.ok) {
                        const data = await res.json();
                        const updatedText = data.content || newText;
                        document.querySelectorAll(`#comment-body-${commentId}, #comment-${commentId} .comment-body`).forEach(bEl => {
                            bEl.textContent = updatedText;
                            bEl.style.display = '';
                        });
                        const editMode = commentEl?.querySelector(`#edit-mode-${commentId}`) || document.getElementById(`edit-mode-${commentId}`);
                        if (editMode) editMode.remove();
                        showCommentsToast('Comment updated');
                    } else {
                        saveEditBtn.disabled = false;
                        saveEditBtn.textContent = 'Save';
                        showCommentsToast('Failed to update comment');
                    }
                } catch (err) {
                    console.error('[CommentsSheet] Save edit error:', err);
                    saveEditBtn.disabled = false;
                    saveEditBtn.textContent = 'Save';
                    showCommentsToast('Failed to update comment');
                }
                return;
            }

            // K. Delete Comment
            const deleteBtn = e.target.closest('[data-action="delete"]');
            if (deleteBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                const commentId = deleteBtn.dataset.commentId;
                const commentEl = findCommentEl(commentId, deleteBtn);
                closeAllSheetCommentMenus();

                if (!confirm('Are you sure you want to delete this comment?')) {
                    return;
                }

                try {
                    const res = await fetch(`/api/comments/${commentId}/`, {
                        method: 'DELETE',
                        headers: {
                            'X-CSRFToken': getCsrfToken()
                        }
                    });
                    if (res.ok) {
                        const parentId = commentEl?.dataset?.parentId || commentEl?.dataset?.parentCommentId;
                        document.querySelectorAll(`#comment-${commentId}, [data-comment-id="${commentId}"]`).forEach(el => {
                            if (!el.classList.contains('comment-item')) return;
                            el.style.transition = 'all 0.25s ease';
                            el.style.opacity = '0';
                            el.style.transform = 'translateX(20px)';
                            setTimeout(() => el.remove(), 250);
                        });

                        setTimeout(() => {
                            if (parentId) {
                                const parentComment = listEl.querySelector(`#comment-${parentId}, [data-comment-id="${parentId}"]`) ||
                                                      document.getElementById(`comment-${parentId}`);
                                const toggle = parentComment?.querySelector('[data-action="toggle-replies"]');
                                if (toggle) {
                                    let rCount = Math.max(0, (parseInt(toggle.dataset.replyCount, 10) || 1) - 1);
                                    toggle.dataset.replyCount = rCount;
                                    const span = toggle.querySelector('span');
                                    if (rCount === 0) {
                                        toggle.style.display = 'none';
                                    } else if (span) {
                                        span.textContent = `View ${rCount} ${rCount === 1 ? 'reply' : 'replies'}`;
                                    }
                                }
                            }

                            const railCountEl = document.getElementById('fsRailCommentCount');
                            const fsDesktopCountEl = document.getElementById('fsDesktopCommentCount');
                            const modalCountEl = document.getElementById('commentsModalCount');
                            const baseEl = railCountEl || modalCountEl || fsDesktopCountEl;
                            let curN = Math.max(0, (parseInt(baseEl?.textContent || '1', 10) || 1) - 1);
                            if (railCountEl) railCountEl.textContent = curN;
                            if (fsDesktopCountEl) fsDesktopCountEl.textContent = curN;
                            if (modalCountEl) modalCountEl.textContent = curN;

                            const inlinePostCount = document.getElementById(`comment-count-${curPostId}`);
                            const inlineReelCount = document.getElementById(`reel-comment-count-${curPostId}`);
                            const fsReelCount = document.getElementById(`fs-reel-comment-count-${curPostId}`);
                            if (inlinePostCount) inlinePostCount.textContent = curN;
                            if (inlineReelCount) inlineReelCount.textContent = curN;
                            if (fsReelCount) fsReelCount.textContent = curN;
                        }, 250);

                        showCommentsToast('Comment deleted');
                    } else {
                        showCommentsToast('Could not delete comment');
                    }
                } catch (err) {
                    console.error('[CommentsSheet] Delete error:', err);
                    showCommentsToast('Could not delete comment');
                }
                return;
            }
        });
    }

    function openCommentsSheet(postId) {
        postId = postId ? String(postId).trim() : '';
        if (!postId || postId === 'null' || postId === 'undefined') {
            console.warn('[CommentsSheet] Aborting openCommentsSheet on invalid postId:', postId);
            return;
        }
        activeCommentsPostId = postId;

        const modalEl = document.getElementById('commentsModal');
        if (!modalEl || typeof bootstrap === 'undefined') {
            console.warn('[CommentsSheet] Comments modal or bootstrap is unavailable.');
            return;
        }

        const countEl = document.getElementById('commentsModalCount');
        const loadingEl = document.getElementById('commentsModalLoading');
        const listEl = document.getElementById('commentsModalList');
        const formEl = document.getElementById('reelCommentForm');
        const inputEl = document.getElementById('reelCommentInput');
        const parentInput = document.getElementById('commentsModalParentId');
        const replyBanner = document.getElementById('commentsModalReplyBanner');

        // Reset reply state
        if (parentInput) parentInput.value = '';
        if (replyBanner) replyBanner.classList.add('d-none');
        if (inputEl) {
            inputEl.value = '';
            inputEl.placeholder = 'Add a comment...';
        }

        if (loadingEl) loadingEl.style.display = 'block';
        if (listEl) {
            listEl.innerHTML = '';
            listEl.dataset.activePostId = postId;
            initSheetCommentsInteractions(listEl, postId);
        }

        // Read current count from postcard or reelcard
        const curCount = document.getElementById(`comment-count-${postId}`)?.textContent ||
                         document.getElementById(`reel-comment-count-${postId}`)?.textContent ||
                         document.getElementById(`fs-reel-comment-count-${postId}`)?.textContent ||
                         document.getElementById('fsRailCommentCount')?.textContent || '0';
        if (countEl) countEl.textContent = curCount.trim();

        // Fetch comments dynamically without navigating away
        fetch(`/post/${postId}/?show_all=1`, {
            headers: { 'HX-Request': 'true' }
        })
        .then(res => res.text())
        .then(html => {
            if (activeCommentsPostId !== postId) return;
            if (loadingEl) loadingEl.style.display = 'none';
            if (listEl) {
                listEl.innerHTML = html;
                reinitHtmxElement(listEl);
                initSheetCommentsInteractions(listEl, postId);
            }
        })
        .catch(err => {
            if (activeCommentsPostId !== postId) return;
            console.warn('[CommentsSheet] Error loading comments:', err);
            if (loadingEl) loadingEl.style.display = 'none';
            if (listEl) listEl.innerHTML = '<div class="text-center py-4 text-muted small">Could not load comments.</div>';
        });

        // Bind comment submission with double-submission protection
        if (formEl && !formEl.dataset.bound) {
            formEl.dataset.bound = 'true';
            formEl.onsubmit = function(e) {
                e.preventDefault();
                if (formEl.dataset.submitting === 'true') return;

                const curPostId = activeCommentsPostId;
                if (!curPostId) return;

                const content = inputEl?.value?.trim() || '';
                const attachmentType = formEl.querySelector('[name="attachment_type"]')?.value || '';
                const attachmentUrl = formEl.querySelector('[name="attachment_url"]')?.value || '';
                const attachmentMeta = formEl.querySelector('[name="attachment_meta"]')?.value || '';
                const photoInput = formEl.querySelector('.comment-photo-input') || formEl.querySelector('input[type="file"][name="attachment_image"]');
                const photoFile = (photoInput && photoInput.files && photoInput.files.length > 0) ? photoInput.files[0] : (formEl._pastedAttachmentFile || null);

                if (!content && !attachmentType && !photoFile) return;

                formEl.dataset.submitting = 'true';
                const sendBtn = formEl.querySelector('button[type="submit"]');
                if (sendBtn) sendBtn.disabled = true;

                const targetParentId = (parentInput && parentInput.value) ? parentInput.value : '';

                const csrfToken = getCsrfToken(formEl);
                const formData = new FormData(formEl);
                formData.set('content', content);
                formData.set('csrfmiddlewaretoken', csrfToken);
                if (targetParentId) {
                    formData.set('parent_id', targetParentId);
                }
                if (attachmentType) {
                    formData.set('attachment_type', attachmentType);
                    if (attachmentUrl) formData.set('attachment_url', attachmentUrl);
                    if (attachmentMeta) formData.set('attachment_meta', attachmentMeta);
                }
                if (photoFile) {
                    formData.set('attachment_image', photoFile);
                    if (!attachmentType) formData.set('attachment_type', 'image');
                }

                fetch(`/post/${curPostId}/comment/`, {
                    method: 'POST',
                    body: formData,
                    headers: {
                        'HX-Request': 'true',
                        'X-CSRFToken': csrfToken
                    }
                })
                .then(res => res.text())
                .then(html => {
                    delete formEl.dataset.submitting;
                    if (sendBtn) sendBtn.disabled = false;
                    if (inputEl) inputEl.value = '';
                    if (parentInput) parentInput.value = '';
                    if (replyBanner) replyBanner.classList.add('d-none');
                    if (window.MediaPicker) {
                        window.MediaPicker.clearAttachment(formEl);
                    }

                    if (listEl) {
                        listEl.innerHTML = html;
                        reinitHtmxElement(listEl);
                        initSheetCommentsInteractions(listEl, curPostId);
                    }

                    // Update all counters
                    let curN = parseInt(countEl?.textContent || '0', 10) || 0;
                    let newN = curN + 1;
                    if (countEl) countEl.textContent = newN;

                    const inlinePostCount = document.getElementById(`comment-count-${curPostId}`);
                    const inlineReelCount = document.getElementById(`reel-comment-count-${curPostId}`);
                    const fsReelCount = document.getElementById(`fs-reel-comment-count-${curPostId}`);
                    if (inlinePostCount) inlinePostCount.textContent = newN;
                    if (inlineReelCount) inlineReelCount.textContent = newN;
                    if (fsReelCount) fsReelCount.textContent = newN;

                    // If it was a reply, auto-expand that parent comment's replies thread
                    if (targetParentId) {
                        const parentEl = document.getElementById(`comment-${targetParentId}`);
                        const toggleBtn = parentEl?.querySelector('[data-action="toggle-replies"]');
                        if (toggleBtn) {
                            toggleBtn.click();
                            setTimeout(() => {
                                parentEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                            }, 250);
                        }
                    } else {
                        // Scroll to bottom of comments list to view new comment
                        const modalBody = document.getElementById('commentsModalBody');
                        if (modalBody) {
                            modalBody.scrollTo({ top: modalBody.scrollHeight, behavior: 'smooth' });
                        }
                    }
                })
                .catch(err => {
                    delete formEl.dataset.submitting;
                    if (sendBtn) sendBtn.disabled = false;
                    console.error('[CommentsSheet] Comment post error:', err);
                });
            };
        }

        // Cancel reply button
        const cancelReplyBtn = document.getElementById('commentsModalCancelReply');
        if (cancelReplyBtn && !cancelReplyBtn.dataset.bound) {
            cancelReplyBtn.dataset.bound = 'true';
            cancelReplyBtn.onclick = function() {
                if (parentInput) parentInput.value = '';
                if (replyBanner) replyBanner.classList.add('d-none');
                if (inputEl && /^@\w+\s*$/.test(inputEl.value)) {
                    inputEl.value = '';
                    inputEl.placeholder = 'Add a comment...';
                }
            };
        }

        bootstrap.Modal.getOrCreateInstance(modalEl).show();
    }

    const openReelComments = openCommentsSheet;

    // ============================================================================
    // SCRUBBER DRAG & JUMP CONTROLLER
    // ============================================================================

    const scrubberState = {
        isDragging: false,
        activeContainer: null,
        activeVideo: null,
        wasPlaying: false
    };

    function updateScrubPosition(clientX) {
        if (!scrubberState.isDragging || !scrubberState.activeContainer) return;

        const rect = scrubberState.activeContainer.getBoundingClientRect();
        if (rect.width === 0) return;

        const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));

        const bar = scrubberState.activeContainer.querySelector('.reel-progress-bar');
        if (bar) {
            bar.style.width = `${ratio * 100}%`;
        }

        if (scrubberState.activeVideo && scrubberState.activeVideo.duration) {
            scrubberState.activeVideo.currentTime = ratio * scrubberState.activeVideo.duration;
        }
    }

    function startScrubbing(container, clientX) {
        const card = container.closest('.reel-card-container, .reel-post-card, .reels-snap-item');
        const video = card?.querySelector('video');
        if (!video) return;

        scrubberState.isDragging = true;
        scrubberState.activeContainer = container;
        scrubberState.activeVideo = video;
        scrubberState.wasPlaying = !video.paused;

        if (scrubberState.wasPlaying) {
            pauseVideo(video);
        }

        container.classList.add('is-dragging');
        updateScrubPosition(clientX);
    }

    function stopScrubbing() {
        if (!scrubberState.isDragging) return;

        if (scrubberState.activeContainer) {
            scrubberState.activeContainer.classList.remove('is-dragging');
        }

        if (scrubberState.activeVideo && scrubberState.wasPlaying) {
            playVideo(scrubberState.activeVideo, false);
        }

        scrubberState.isDragging = false;
        scrubberState.activeContainer = null;
        scrubberState.activeVideo = null;
        scrubberState.wasPlaying = false;
    }

    function handleScrubberClick(container, event) {
        const rect = container.getBoundingClientRect();
        if (rect.width === 0) return;
        const clickX = event.clientX - rect.left;
        const ratio = Math.max(0, Math.min(1, clickX / rect.width));
        const card = container.closest('.reel-card-container, .reel-post-card, .reels-snap-item');
        const video = card?.querySelector('video');
        if (video && video.duration) {
            video.currentTime = ratio * video.duration;
            const bar = container.querySelector('.reel-progress-bar');
            if (bar) bar.style.width = `${ratio * 100}%`;
        }
    }

    // ============================================================================
    // INITIALIZATION & EVENT DELEGATION
    // ============================================================================

    function setupDelegatedListeners() {
        // --------------------------------------------------------------------
        // Global Gesture Discrimination: Swipe vs. Tap (Phase 2)
        // Strictly prevents swipe/drag gestures from generating accidental clicks
        // --------------------------------------------------------------------
        let gestureStartX = 0;
        let gestureStartY = 0;
        let dragResetTimer = null;
        const DRAG_THRESHOLD_PX = 18;

        document.addEventListener('touchstart', function(e) {
            if (e.touches && e.touches.length === 1) {
                gestureStartX = e.touches[0].clientX;
                gestureStartY = e.touches[0].clientY;
                isGestureDrag = false;
                if (dragResetTimer) {
                    clearTimeout(dragResetTimer);
                    dragResetTimer = null;
                }
            }
        }, { passive: true, capture: true });

        document.addEventListener('touchmove', function(e) {
            if (!isGestureDrag && e.touches && e.touches.length === 1) {
                const dx = Math.abs(e.touches[0].clientX - gestureStartX);
                const dy = Math.abs(e.touches[0].clientY - gestureStartY);
                if (dx > DRAG_THRESHOLD_PX || dy > DRAG_THRESHOLD_PX) {
                    isGestureDrag = true;
                }
            }
        }, { passive: true, capture: true });

        document.addEventListener('touchend', function() {
            if (isGestureDrag) {
                clearTimeout(dragResetTimer);
                dragResetTimer = setTimeout(() => {
                    isGestureDrag = false;
                    dragResetTimer = null;
                }, 120);
            }
        }, { passive: true, capture: true });

        document.addEventListener('touchcancel', function() {
            isGestureDrag = false;
            if (dragResetTimer) {
                clearTimeout(dragResetTimer);
                dragResetTimer = null;
            }
        }, { passive: true, capture: true });

        // Capturing click filter: discard synthetic clicks caused by finger swipes/drags
        document.addEventListener('click', function(event) {
            if (isGestureDrag) {
                const target = event.target;
                const isSwipeableTarget = target && target.closest && (
                    target.closest('.reels-carousel-card') ||
                    target.closest('.reels-carousel-shelf') ||
                    target.closest('.reel-center-hitbox') ||
                    target.closest('.reel-tap-hitbox') ||
                    target.closest('.reels-snap-item') ||
                    target.closest('#reelsSnapViewport') ||
                    target.closest('.reel-card-container') ||
                    target.closest('.reel-post-card') ||
                    target.closest('.post-card') ||
                    target.closest('.cursor-pointer')
                );
                if (isSwipeableTarget) {
                    event.preventDefault();
                    event.stopPropagation();
                    event.stopImmediatePropagation();
                    return;
                }
            }
        }, true);

        // Tap and Hitbox handling
        document.addEventListener('click', function(event) {
            const followChip = event.target.closest('.reel-follow-chip');
            if (followChip) {
                event.preventDefault();
                event.stopPropagation();
                if (followChip.disabled) return;

                const originalLabel = followChip.textContent.trim() || 'Follow';
                const authorUsername = followChip.dataset.authorUsername || '';
                const followUrl = followChip.dataset.followUrl || '';
                followChip.disabled = true;
                followChip.classList.add('is-loading');
                followChip.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>Following…</span>';

                fetch(followUrl, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'X-CSRFToken': getCsrfToken(),
                        'X-Requested-With': 'XMLHttpRequest',
                        'Accept': 'application/json'
                    }
                }).then(response => {
                    if (!response.ok) throw new Error('Follow request failed');
                    return response.json();
                }).then(data => {
                    if (!data || data.is_following !== true) throw new Error('Follow was not confirmed');

                    document.querySelectorAll('[data-author-username]').forEach(element => {
                        if (element.dataset.authorUsername === authorUsername) {
                            element.dataset.isFollowing = 'true';
                        }
                    });
                    document.querySelectorAll('.reel-follow-chip[data-author-username]').forEach(chip => {
                        if (chip.dataset.authorUsername !== authorUsername) return;
                        chip.disabled = true;
                        chip.classList.remove('is-loading');
                        chip.classList.add('is-following');
                        chip.textContent = 'Following';
                        chip.title = 'Following';
                        chip.setAttribute('aria-label', `Following ${authorUsername}`);
                    });
                }).catch(error => {
                    console.warn('[VideoManager] Follow request failed:', error);
                    followChip.disabled = false;
                    followChip.classList.remove('is-loading');
                    followChip.textContent = originalLabel;
                    followChip.title = 'Follow';
                    followChip.setAttribute('aria-label', `Follow ${authorUsername}`);
                });
                return;
            }

            // Hitbox single vs. double tap (pauses/plays or likes)
            const hitbox = event.target.closest('.reel-center-hitbox, .reel-tap-hitbox');
            if (hitbox) {
                if (isGestureDrag) return;
                event.preventDefault();
                event.stopPropagation();
                handleHitboxTap(hitbox, event);
                return;
            }

            // Direct tap fallback on video element, stage container, or ambient backdrop
            const videoElem = event.target.closest('video');
            const stageContainer = event.target.closest('.reel-stage-container, .landscape-video-container, .landscape-video-wrapper, .reel-fullscreen-content');
            if ((videoElem || stageContainer) && !event.target.closest('.reels-carousel-shelf, .reel-actions-stack, .reel-desktop-actions-rail, .reel-top-action-btn, .dropdown, .modal, button, a')) {
                if (isGestureDrag) return;
                const container = (videoElem || stageContainer).closest('.reel-card-container, .reel-post-card, .reel-stage-container, .reel-fullscreen-content, .reels-snap-item, .media-video-container, .landscape-video-container, .landscape-video-wrapper, .post-card, .post-media-wrapper');
                const targetHitbox = container ? container.querySelector('.reel-center-hitbox, .reel-tap-hitbox') : null;
                event.preventDefault();
                event.stopPropagation();
                handleHitboxTap(targetHitbox || container || videoElem, event);
                return;
            }

            // Play HUD tap (direct tap on central play button overlay when paused)
            const playHud = event.target.closest('.reel-play-hud');
            if (playHud && (playHud.classList.contains('show') || playHud.classList.contains('is-paused'))) {
                if (isGestureDrag) return;
                event.preventDefault();
                event.stopPropagation();
                const container = playHud.closest('.reel-card-container, .reel-post-card, .reel-stage-container, .reel-fullscreen-content, .reels-snap-item, .media-video-container, .landscape-video-container, .landscape-video-wrapper, .post-card, .post-media-wrapper');
                const video = container ? container.querySelector('video') : null;
                if (video) {
                    if (video.paused) {
                        delete video.dataset.userPaused;
                        playVideo(video, false);
                    } else {
                        video.dataset.userPaused = 'true';
                        pauseVideo(video);
                    }
                }
                return;
            }

            // Mute toggle button
            const muteBtn = event.target.closest('.reel-mute-btn, .video-mute-toggle, [data-action="mute-toggle"]');
            if (muteBtn) {
                event.preventDefault();
                event.stopPropagation();
                toggleGlobalMute();
                return;
            }

            // Carousel Card Click -> Open Fullscreen Reel
            const carouselCard = event.target.closest('.reels-carousel-card');
            if (carouselCard) {
                if (isGestureDrag || event.defaultPrevented) return;
                event.preventDefault();
                event.stopPropagation();
                const postId = carouselCard.dataset.postId || extractPostId(carouselCard);
                openFullscreenReels(postId);
                return;
            }

            // Carousel View All Link Click -> Open Fullscreen Reel
            const viewAllLink = event.target.closest('.reels-view-all-link');
            if (viewAllLink) {
                event.preventDefault();
                event.stopPropagation();
                const shelf = viewAllLink.closest('.reels-carousel-shelf');
                const firstCard = shelf?.querySelector('.reels-carousel-card');
                const postId = firstCard ? (firstCard.dataset.postId || extractPostId(firstCard)) : null;
                openFullscreenReels(postId);
                return;
            }

            // Expand to Fullscreen Reel (Only clicking this button opens fullscreen mode)
            const expandBtn = event.target.closest('.reel-expand-btn, [data-action="fullscreen"]');
            if (expandBtn) {
                event.preventDefault();
                event.stopPropagation();
                const postId = extractPostId(expandBtn);
                openFullscreenReels(postId);
                return;
            }

            // Desktop Next / Previous Reel buttons
            const navNext = event.target.closest('#reelsNavNext');
            if (navNext) {
                event.preventDefault();
                event.stopPropagation();
                if (!window.isAuthenticated) {
                    if (typeof window.showGuestAuthPrompt === 'function') {
                        window.showGuestAuthPrompt({
                            title: 'Watch more reels on vasto',
                            subtitle: 'Sign in to watch endless campus reels, trending videos, and discover creators.',
                            icon: 'bi-play-circle-fill'
                        });
                    }
                    return;
                }
                const viewport = document.getElementById('reelsSnapViewport');
                if (viewport && viewport.children.length > 0) {
                    const h = viewport.clientHeight;
                    const curIdx = Math.round(viewport.scrollTop / h);
                    const nextIdx = Math.min(viewport.children.length - 1, curIdx + 1);
                    viewport.scrollTo({ top: nextIdx * h, behavior: 'smooth' });
                    updateFullscreenNavButtons(nextIdx);
                }
                return;
            }

            const navPrev = event.target.closest('#reelsNavPrev');
            if (navPrev) {
                event.preventDefault();
                event.stopPropagation();
                const viewport = document.getElementById('reelsSnapViewport');
                if (viewport && viewport.children.length > 0) {
                    const h = viewport.clientHeight;
                    const curIdx = Math.round(viewport.scrollTop / h);
                    const prevIdx = Math.max(0, curIdx - 1);
                    viewport.scrollTo({ top: prevIdx * h, behavior: 'smooth' });
                    updateFullscreenNavButtons(prevIdx);
                }
                return;
            }

            // Comment trigger button (both reels and postcards)
            const commentBtn = event.target.closest('.reel-comment-trigger, .postcard-comment-btn, [data-action="open-comments"], #fsDesktopCommentBtn, #reelCommentRailToggleBtn');
            if (commentBtn) {
                event.preventDefault();
                event.stopPropagation();
                if (state.isFullScreenActive && window.innerWidth >= 992) {
                    toggleDesktopCommentRail(true);
                    const sideInput = document.getElementById('fsRailCommentInput');
                    if (sideInput) sideInput.focus();
                    return;
                }
                const rawPostId = commentBtn.dataset.postId || extractPostId(commentBtn);
                const postId = rawPostId ? String(rawPostId).trim() : '';
                if (!postId || postId === 'null' || postId === 'undefined') return;
                openCommentsSheet(postId);
                return;
            }

            // Options trigger button
            const optionsBtn = event.target.closest('.reel-options-btn');
            if (optionsBtn) {
                event.preventDefault();
                event.stopPropagation();
                const rawPostId = optionsBtn.dataset.postId || extractPostId(optionsBtn);
                const postId = rawPostId ? String(rawPostId).trim() : '';
                if (!postId || postId === 'null' || postId === 'undefined') return;
                openReelQuickTools(postId, optionsBtn);
                return;
            }

            // Micro-scrubber click (instant jump if not dragged)
            const scrubber = event.target.closest('.reel-progress-container');
            if (scrubber) {
                event.preventDefault();
                event.stopPropagation();
                handleScrubberClick(scrubber, event);
                return;
            }

            // Close Fullscreen Reel
            const closeBtn = event.target.closest('#closeReelsOverlay, .close-fs-reels-btn');
            if (closeBtn) {
                event.preventDefault();
                event.stopPropagation();
                closeFullscreenReels();
                return;
            }

            // Fullscreen Like button
            const fsLikeBtn = event.target.closest('.fs-like-proxy-btn');
            if (fsLikeBtn) {
                event.preventDefault();
                event.stopPropagation();
                const postId = fsLikeBtn.dataset.postId || currentDesktopRailPostId;
                if (!postId) return;
                toggleReelLike(postId);
                return;
            }

            // Fullscreen Repost button
            const fsRepostBtn = event.target.closest('.fs-repost-proxy-btn');
            if (fsRepostBtn) {
                event.preventDefault();
                event.stopPropagation();
                const postId = fsRepostBtn.dataset.postId || currentDesktopRailPostId;
                if (!postId) return;
                toggleReelRepost(postId);
                return;
            }
        });

        // Real-time WebSocket Feed Metric Listeners
        window.addEventListener('feedPostLikeUpdate', function(e) {
            const data = e.detail;
            if (!data) return;
            const targetId = data.share_id || data.post_id;
            const currentUserId = window.vastoUserId || document.body.dataset.userId;
            const isCurrentUser = Boolean(data.user_id && currentUserId && String(data.user_id) === String(currentUserId));
            if (targetId) {
                const isLiked = isCurrentUser ? data.is_liked : undefined;
                syncLikeUiAcrossSite(targetId, isLiked, data.like_count);
            }
        });

        window.addEventListener('feedPostRepostUpdate', function(e) {
            const data = e.detail;
            if (!data) return;
            const targetId = data.share_id || data.post_id;
            const currentUserId = window.vastoUserId || document.body.dataset.userId;
            const isCurrentUser = Boolean(data.user_id && currentUserId && String(data.user_id) === String(currentUserId));
            if (targetId) {
                const isReposted = isCurrentUser ? data.is_reposted : undefined;
                syncRepostUiAcrossSite(targetId, isReposted, data.repost_count, data);
            }
        });

        // Scrubber Dragging Listeners (Desktop Mouse Drag & Mobile Touch Scrubbing)
        function onScrubberTouchMove(event) {
            if (scrubberState.isDragging && event.touches.length > 0) {
                if (event.cancelable) event.preventDefault();
                updateScrubPosition(event.touches[0].clientX);
            }
        }

        function finishTouchScrubbing() {
            document.removeEventListener('touchmove', onScrubberTouchMove);
            if (scrubberState.isDragging) {
                stopScrubbing();
            }
        }

        document.addEventListener('mousedown', function(event) {
            const scrubber = event.target.closest('.reel-progress-container');
            if (scrubber) {
                event.preventDefault();
                event.stopPropagation();
                startScrubbing(scrubber, event.clientX);
            }
        });

        document.addEventListener('touchstart', function(event) {
            const scrubber = event.target.closest('.reel-progress-container');
            if (scrubber && event.touches.length > 0) {
                event.stopPropagation();
                startScrubbing(scrubber, event.touches[0].clientX);
                document.addEventListener('touchmove', onScrubberTouchMove, { passive: false });
            }
        }, { passive: true });

        document.addEventListener('mousemove', function(event) {
            if (scrubberState.isDragging) {
                event.preventDefault();
                updateScrubPosition(event.clientX);
            }
        });

        document.addEventListener('mouseup', function() {
            if (scrubberState.isDragging) {
                stopScrubbing();
            }
        });

        document.addEventListener('touchend', finishTouchScrubbing, { passive: true });
        document.addEventListener('touchcancel', finishTouchScrubbing, { passive: true });

        // Long-press detection on reel cards and hitboxes
        let longPressTimer = null;
        let touchStartX = 0;
        let touchStartY = 0;
        let longPressMoved = false;

        function cancelReelLongPress() {
            longPressMoved = true;
            if (longPressTimer) {
                clearTimeout(longPressTimer);
                longPressTimer = null;
            }
        }

        document.addEventListener('touchstart', function(event) {
            cancelReelLongPress();
            if (!event.touches || event.touches.length !== 1) return;
            const hitbox = event.target.closest('.reel-center-hitbox, .reel-tap-hitbox, .reel-stage-container, .reel-card-container');
            if (!hitbox) return;

            const touch = event.touches[0];
            touchStartX = touch.clientX;
            touchStartY = touch.clientY;
            longPressMoved = false;
            state.hasLongPressed = false;

            longPressTimer = setTimeout(() => {
                longPressTimer = null;
                if (longPressMoved) return;
                state.hasLongPressed = true;
                if (window.Haptics && typeof window.Haptics.impactMedium === 'function') {
                    window.Haptics.impactMedium();
                } else if (navigator.vibrate) {
                    navigator.vibrate(20);
                }
                const postId = extractPostId(hitbox);
                if (postId) {
                    openReelQuickTools(postId, hitbox);
                }
            }, 520);
        }, { passive: true });

        document.addEventListener('touchmove', function(event) {
            if (!longPressTimer || !event.touches || event.touches.length === 0) return;
            const touch = event.touches[0];
            if (Math.abs(touch.clientX - touchStartX) > 8 || Math.abs(touch.clientY - touchStartY) > 8) {
                cancelReelLongPress();
            }
        }, { passive: true });

        document.addEventListener('touchend', cancelReelLongPress, { passive: true });
        document.addEventListener('touchcancel', cancelReelLongPress, { passive: true });
        window.addEventListener('scroll', cancelReelLongPress, { capture: true, passive: true });

        // Right click context menu on desktop -> opens quick tools
        document.addEventListener('contextmenu', function(event) {
            const card = event.target.closest('.reel-stage-container, .reel-card-container, .reel-post-card, .reels-carousel-card');
            if (card) {
                event.preventDefault();
                // Only open quick tools from a desktop mouse right-click, not touch scroll/hold
                if (event.pointerType === 'mouse' || (!('ontouchstart' in window) && event.button === 2)) {
                    const postId = extractPostId(card);
                    if (postId) openReelQuickTools(postId, card);
                }
            }
        });

        // Fullscreen keyboard navigation (Escape, Up/Down arrows, Page Up/Down, Space play/pause, M mute)
        document.addEventListener('keydown', function(event) {
            if (!state.isFullScreenActive) return;

            // Don't intercept typing in inputs/textareas
            if (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA' || event.target.isContentEditable) {
                return;
            }

            if (event.key === 'Escape') {
                event.preventDefault();
                // Esc closes the shortcuts panel first — never yank the player
                // away while someone is just reading the cheat-sheet.
                if (reelKbPanelOpen) {
                    toggleReelKbPanel(false);
                    return;
                }
                closeFullscreenReels();
                return;
            }

            // '?' (Shift + /) — YouTube parity: open/close the shortcuts panel
            if (event.key === '?' || (event.shiftKey && event.key === '/')) {
                event.preventDefault();
                toggleReelKbPanel();
                return;
            }

            const viewport = document.getElementById('reelsSnapViewport');
            if (!viewport) return;

            if (event.key === 'ArrowDown' || event.key === 'PageDown' || event.key.toLowerCase() === 'j') {
                event.preventDefault();
                if (!window.isAuthenticated) {
                    if (typeof window.showGuestAuthPrompt === 'function') {
                        window.showGuestAuthPrompt({
                            title: 'Watch more reels on vasto',
                            subtitle: 'Sign in to watch endless campus reels, trending videos, and discover creators.',
                            icon: 'bi-play-circle-fill'
                        });
                    }
                    return;
                }
                const h = viewport.clientHeight || 1;
                const curIdx = Math.round(viewport.scrollTop / h);
                const nextIdx = Math.min(viewport.children.length - 1, curIdx + 1);
                viewport.scrollTo({ top: nextIdx * h, behavior: 'smooth' });
                updateFullscreenNavButtons(nextIdx);
                return;
            }

            if (event.key === 'ArrowUp' || event.key === 'PageUp' || event.key.toLowerCase() === 'k') {
                event.preventDefault();
                const h = viewport.clientHeight || 1;
                const curIdx = Math.round(viewport.scrollTop / h);
                const prevIdx = Math.max(0, curIdx - 1);
                viewport.scrollTo({ top: prevIdx * h, behavior: 'smooth' });
                updateFullscreenNavButtons(prevIdx);
                return;
            }

            if (event.key === ' ' || event.code === 'Space') {
                event.preventDefault();
                // pauseVideo() nulls state.currentPlayingVideo, so after a
                // Space-pause the resume path must resolve the video from the
                // active snap item instead of the (now null) state pointer.
                let targetVideo = state.currentPlayingVideo;
                if (!targetVideo || targetVideo.paused) {
                    const activeItem = getActiveSnapItem();
                    if (activeItem) {
                        const activeVid = activeItem.querySelector('video');
                        if (activeVid) targetVideo = activeVid;
                    }
                }
                if (targetVideo) {
                    const snapItem = targetVideo.closest('.reels-snap-item');
                    if (targetVideo.paused) {
                        playVideo(targetVideo, false);
                        if (snapItem) triggerPlayHud(snapItem, true);
                    } else {
                        pauseVideo(targetVideo);
                        if (snapItem) triggerPlayHud(snapItem, false);
                    }
                }
                return;
            }

            if (!event.ctrlKey && !event.metaKey && !event.altKey) {
                if (event.key.toLowerCase() === 'm') {
                    event.preventDefault();
                    toggleGlobalMute();
                    return;
                }

                if (event.key.toLowerCase() === 'c') {
                    event.preventDefault();
                    toggleDesktopCommentRail();
                    return;
                }
            }
        });

        // Timeupdate to sync micro-scrubber progress bars
        document.addEventListener('timeupdate', function(event) {
            const video = event.target;
            if (video.tagName !== 'VIDEO') return;
            if (!video.duration) return;

            const pct = (video.currentTime / video.duration) * 100;
            const card = video.closest('.reel-card-container, .reel-post-card, .reels-snap-item');
            if (card) {
                const bar = card.querySelector('.reel-progress-bar');
                if (bar) {
                    bar.style.width = `${pct}%`;
                }
            }
        }, true);

        // Sync vinyl disc animation on play
        document.addEventListener('play', function(event) {
            const video = event.target;
            if (video.tagName !== 'VIDEO') return;
            // The blurred letterbox filler never owns playback state.
            if (video.classList.contains('reel-ambient-video')) return;

            if (state.currentPlayingVideo && state.currentPlayingVideo !== video) {
                pauseVideo(state.currentPlayingVideo);
            }
            state.currentPlayingVideo = video;

            const card = video.closest('.reel-card-container, .reel-post-card, .reels-snap-item');
            if (card) {
                const vinyl = card.querySelector('.reel-vinyl-disc');
                if (vinyl) vinyl.classList.add('is-playing');
            }
        }, true);

        // Sync vinyl disc animation on pause
        document.addEventListener('pause', function(event) {
            const video = event.target;
            if (video.tagName !== 'VIDEO') return;
            // The blurred letterbox filler never owns playback state.
            if (video.classList.contains('reel-ambient-video')) return;

            const card = video.closest('.reel-card-container, .reel-post-card, .reels-snap-item');
            if (card) {
                const vinyl = card.querySelector('.reel-vinyl-disc');
                if (vinyl) vinyl.classList.remove('is-playing');
            }
        }, true);

        // Auto-scroll: advance to next reel when video ends (fullscreen mode only)
        // For looping videos (loop attr), we detect near-completion instead of 'ended'
        document.addEventListener('ended', function(event) {
            const video = event.target;
            if (video.tagName !== 'VIDEO') return;
            // The blurred letterbox filler never drives reel navigation.
            if (video.classList.contains('reel-ambient-video')) return;
            if (!isAutoScrollEnabled || !state.isFullScreenActive) return;
            if (video.closest('.reels-carousel-shelf')) return;

            // Small delay so the last frame doesn't snap away too abruptly
            setTimeout(scrollToNextReel, 400);
        }, true);

        // For videos with loop=true, 'ended' never fires — detect loop restart via timeupdate
        // We schedule the scroll right before the video would loop (last 0.35 s)
        let _autoScrollLoopTimer = null;
        document.addEventListener('timeupdate', function(event) {
            const video = event.target;
            if (video.tagName !== 'VIDEO') return;
            // The blurred letterbox filler is a copy — it must never drive
            // reel navigation (it is in sync with the main reel by design).
            if (video.classList.contains('reel-ambient-video')) return;
            if (!isAutoScrollEnabled || !state.isFullScreenActive) return;
            if (!video.loop || !video.duration) return;
            if (video.closest('.reels-carousel-shelf')) return;

            const remaining = video.duration - video.currentTime;
            if (remaining <= 0.35 && remaining > 0 && !_autoScrollLoopTimer) {
                _autoScrollLoopTimer = setTimeout(() => {
                    _autoScrollLoopTimer = null;
                    if (isAutoScrollEnabled && state.isFullScreenActive) {
                        scrollToNextReel();
                    }
                }, Math.max(0, remaining * 1000));
            } else if (video.currentTime < 0.5 && _autoScrollLoopTimer) {
                // Video looped before our timer fired — clear it
                clearTimeout(_autoScrollLoopTimer);
                _autoScrollLoopTimer = null;
            }
        }, true);

        // Pause on visibility change
        document.addEventListener('visibilitychange', function() {
            if (document.hidden && state.currentPlayingVideo) {
                pauseVideo(state.currentPlayingVideo);
            }
        });

        // Passive scroll and snap-settlement listener on snap viewport (Phase 2)
        const snapViewportEl = document.getElementById('reelsSnapViewport');
        if (snapViewportEl) {
            let scrollNavTimer = null;
            let snapSettleTimer = null;

            const handleSnapSettled = () => {
                if (!state.isFullScreenActive) return;
                const h = snapViewportEl.clientHeight || 1;
                const curIdx = Math.round(snapViewportEl.scrollTop / h);
                const items = Array.from(snapViewportEl.children);
                if (curIdx >= 0 && curIdx < items.length) {
                    if (curIdx !== reelSlotEngine.activeIndex) {
                        updateThreeSlotWindow(curIdx);
                        _tickPromptSessionCounter();
                    }
                    const activeSnap = items[curIdx];
                    if (activeSnap) {
                        adaptSnapItemOrientation(activeSnap);
                    }
                    updateFullscreenNavButtons(activeSnap);
                }
            };

            snapViewportEl.addEventListener('scroll', function() {
                if (!state.isFullScreenActive) return;
                clearTimeout(scrollNavTimer);
                clearTimeout(snapSettleTimer);

                scrollNavTimer = setTimeout(() => {
                    updateFullscreenNavButtons();
                    const activeSnap = getActiveSnapItem();
                    if (activeSnap) {
                        adaptSnapItemOrientation(activeSnap);
                    }
                }, 40);

                // Debounced snap settlement (fallback for older browser engines)
                snapSettleTimer = setTimeout(handleSnapSettled, 60);
            }, { passive: true });

            snapViewportEl.addEventListener('scrollend', function() {
                if (!state.isFullScreenActive) return;
                clearTimeout(snapSettleTimer);
                handleSnapSettled();
            }, { passive: true });

            // Guest reels limiter: trigger auth prompt when guest tries to scroll/swipe past the shared reel
            let guestWheelThrottle = false;
            snapViewportEl.addEventListener('wheel', function(e) {
                if (!window.isAuthenticated && e.deltaY > 15) {
                    if (!guestWheelThrottle && typeof window.showGuestAuthPrompt === 'function') {
                        guestWheelThrottle = true;
                        setTimeout(() => { guestWheelThrottle = false; }, 1200);
                        window.showGuestAuthPrompt({
                            title: 'Watch more reels on vasto',
                            subtitle: 'Sign in to watch endless campus reels, trending videos, and discover creators.',
                            icon: 'bi-play-circle-fill'
                        });
                    }
                }
            }, { passive: true });

            let guestTouchStartY = 0;
            snapViewportEl.addEventListener('touchstart', function(e) {
                if (!window.isAuthenticated && e.touches.length > 0) {
                    guestTouchStartY = e.touches[0].clientY;
                }
            }, { passive: true });

            let guestTouchThrottle = false;
            snapViewportEl.addEventListener('touchend', function(e) {
                if (!window.isAuthenticated && guestTouchStartY > 0 && e.changedTouches.length > 0) {
                    const delta = guestTouchStartY - e.changedTouches[0].clientY;
                    if (delta > 35 && !guestTouchThrottle) {
                        guestTouchThrottle = true;
                        setTimeout(() => { guestTouchThrottle = false; }, 1200);
                        if (typeof window.showGuestAuthPrompt === 'function') {
                            window.showGuestAuthPrompt({
                                title: 'Watch more reels on vasto',
                                subtitle: 'Sign in to watch endless campus reels, trending videos, and discover creators.',
                                icon: 'bi-play-circle-fill'
                            });
                        }
                    }
                    guestTouchStartY = 0;
                }
            }, { passive: true });
        }

        window.addEventListener('resize', function() {
            if (state.isFullScreenActive) {
                const viewport = document.getElementById('reelsSnapViewport');
                if (viewport) {
                    const h = viewport.clientHeight || 1;
                    const curIdx = Math.round(viewport.scrollTop / h);
                    const activeSnap = viewport.children[curIdx] || viewport.firstElementChild;
                    if (activeSnap) {
                        const content = activeSnap.querySelector('.reel-fullscreen-content');
                        if (window.innerWidth < 992) {
                            if (content) {
                                content.style.width = '';
                                content.style.height = '';
                                content.style.maxWidth = '';
                                content.style.maxHeight = '';
                                content.style.aspectRatio = '';
                                content.classList.remove('is-portrait', 'is-landscape', 'is-square');
                            }
                        } else {
                            adaptSnapItemOrientation(activeSnap);
                            updateDesktopSideRail(activeSnap);
                        }
                    }
                }
            }
        });

    }

    // ============================================================================
    // REELS CAROUSEL STICK-AUTOPLAY ENGINE
    // ============================================================================

    let carouselShelfObserver = null;
    let activeCarouselCard = null;
    let carouselStickDebounceTimer = null;

    function stopCarouselAutoplay(resetActive = true) {
        if (carouselStickDebounceTimer) {
            clearTimeout(carouselStickDebounceTimer);
            carouselStickDebounceTimer = null;
        }

        if (activeCarouselCard) {
            const vid = activeCarouselCard.querySelector('video.reels-carousel-video');
            if (vid) {
                try {
                    delete vid.dataset.wantsAutoplay;
                    vid.pause();
                    vid.currentTime = 0;
                    vid.style.opacity = '0';
                    vid.classList.remove('is-playing');
                } catch (e) {}
            }
            if (resetActive) {
                activeCarouselCard = null;
            }
        }
    }

    function playCarouselCard(card) {
        if (!card || state.isFullScreenActive) return;
        const video = card.querySelector('video.reels-carousel-video');
        if (!video) return;

        if (activeCarouselCard === card && !video.paused) {
            video.style.opacity = '1';
            video.classList.add('is-playing');
            return; // already playing
        }

        stopCarouselAutoplay(false);

        activeCarouselCard = card;
        video.muted = true;
        video.volume = 0;
        video.loop = false; // Sequential autoplay: do not loop single card
        video.dataset.wantsAutoplay = 'true';

        // Resume HLS buffer loading if already initialized
        if (video._hlsInstance && typeof video._hlsInstance.startLoad === 'function') {
            try { video._hlsInstance.startLoad(); } catch (_) {}
        }

        // Ensure HLS or source is attached
        if (video.dataset.hlsUrl && !video.dataset.hlsReady && typeof window.initHLSForElement === 'function') {
            window.initHLSForElement(video);
        } else if (!video.src && video.dataset.videoUrl) {
            video.src = video.dataset.videoUrl;
            video.load();
        }

        // Sequential autoplay: advance to next card when current card finishes
        if (!video._carouselEndedBound) {
            video._carouselEndedBound = true;
            video.addEventListener('ended', function handleCarouselCardEnded() {
                if (activeCarouselCard !== card || state.isFullScreenActive) return;
                const shelf = card.closest('.reels-carousel-shelf');
                if (!shelf) return;
                const track = shelf.querySelector('.reels-carousel-track');
                if (!track) return;

                const cards = Array.from(track.querySelectorAll('.reels-carousel-card'));
                const curIdx = cards.indexOf(card);
                const nextIdx = (curIdx !== -1 && curIdx + 1 < cards.length) ? curIdx + 1 : 0;
                const nextCard = cards[nextIdx];
                if (nextCard && nextCard !== card) {
                    nextCard.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
                    playCarouselCard(nextCard);
                }
            });
        }

        const startPlaying = () => {
            if (activeCarouselCard !== card) return;
            video.muted = true;
            video.volume = 0;
            const playPromise = video.play();
            if (playPromise !== undefined) {
                playPromise.then(() => {
                    video.style.opacity = '1';
                    video.classList.add('is-playing');
                }).catch(() => {
                    video.muted = true;
                    video.play().then(() => {
                        video.style.opacity = '1';
                        video.classList.add('is-playing');
                    }).catch(() => {});
                });
            }
        };

        video.addEventListener('playing', () => {
            if (activeCarouselCard === card) {
                video.style.opacity = '1';
                video.classList.add('is-playing');
            }
        });

        if (video.readyState >= 2) {
            startPlaying();
        } else {
            video.addEventListener('canplay', startPlaying, { once: true });
            video.addEventListener('loadeddata', startPlaying, { once: true });
            startPlaying();
        }
    }

    function findCenterCarouselCard(track) {
        if (!track) return null;
        const cards = Array.from(track.querySelectorAll('.reels-carousel-card'));
        if (cards.length === 0) return null;

        const trackRect = track.getBoundingClientRect();
        const centerLine = trackRect.left + trackRect.width / 2;

        let closestCard = null;
        let minDiff = Infinity;

        cards.forEach(card => {
            const rect = card.getBoundingClientRect();
            if (rect.right < trackRect.left || rect.left > trackRect.right) return;

            const cardCenter = rect.left + rect.width / 2;
            const diff = Math.abs(cardCenter - centerLine);
            if (diff < minDiff) {
                minDiff = diff;
                closestCard = card;
            }
        });

        return closestCard || cards[0];
    }

    function scheduleCarouselStickCheck(shelf, delayMs = 80) {
        if (state.isFullScreenActive) return;
        if (carouselStickDebounceTimer) {
            clearTimeout(carouselStickDebounceTimer);
        }

        carouselStickDebounceTimer = setTimeout(() => {
            if (state.isFullScreenActive) return;
            if (!shelf || !shelf.isConnected) return;

            const rect = shelf.getBoundingClientRect();
            const vh = window.innerHeight || document.documentElement.clientHeight;
            const visibleHeight = Math.min(rect.bottom, vh) - Math.max(rect.top, 0);
            if (visibleHeight <= 0 || visibleHeight / rect.height < 0.3) {
                stopCarouselAutoplay();
                return;
            }

            const track = shelf.querySelector('.reels-carousel-track');
            const targetCard = findCenterCarouselCard(track);
            if (targetCard) {
                playCarouselCard(targetCard);
            }
        }, delayMs);
    }

    function initializeCarouselShelves(container = document.body) {
        const shelves = container.querySelectorAll('.reels-carousel-shelf');
        if (shelves.length === 0) return;

        if (!carouselShelfObserver) {
            carouselShelfObserver = new IntersectionObserver((entries) => {
                if (state.isFullScreenActive) return;
                entries.forEach(entry => {
                    const shelf = entry.target;
                    if (entry.isIntersecting && entry.intersectionRatio >= 0.35) {
                        shelf.dataset.inView = 'true';
                        scheduleCarouselStickCheck(shelf, 300);
                    } else if (!entry.isIntersecting || entry.intersectionRatio < 0.25) {
                        shelf.dataset.inView = 'false';
                        if (activeCarouselCard && activeCarouselCard.closest('.reels-carousel-shelf') === shelf) {
                            stopCarouselAutoplay();
                        }
                    }
                });
            }, {
                root: null,
                threshold: [0.1, 0.25, 0.35, 0.5, 0.7]
            });
        }

        shelves.forEach(shelf => {
            if (carouselShelfObserver) {
                try { carouselShelfObserver.observe(shelf); } catch (_) {}
            }
            if (shelf.dataset.carouselInit === 'true') return;
            shelf.dataset.carouselInit = 'true';

            carouselShelfObserver.observe(shelf);

            const track = shelf.querySelector('.reels-carousel-track');
            if (track) {
                track.addEventListener('scroll', () => {
                    if (activeCarouselCard) {
                        stopCarouselAutoplay(false);
                    }
                    if (shelf.dataset.inView === 'true') {
                        scheduleCarouselStickCheck(shelf, 400);
                    }
                }, { passive: true });
            }

            const cards = shelf.querySelectorAll('.reels-carousel-card');
            cards.forEach(card => {
                let hoverTimer = null;
                const onEnter = () => {
                    if (hoverTimer) clearTimeout(hoverTimer);
                    hoverTimer = setTimeout(() => {
                        playCarouselCard(card);
                    }, 50);
                };

                const onLeave = () => {
                    if (hoverTimer) {
                        clearTimeout(hoverTimer);
                        hoverTimer = null;
                    }
                    if (activeCarouselCard === card) {
                        stopCarouselAutoplay();
                        if (shelf.dataset.inView === 'true') {
                            scheduleCarouselStickCheck(shelf, 400);
                        }
                    }
                };

                card.addEventListener('mouseenter', onEnter);
                card.addEventListener('mouseleave', onLeave);
            });
        });
    }

    // Scroll listener on window: debounces stick timer and stops video if shelf leaves viewport
    window.addEventListener('scroll', () => {
        if (state.isFullScreenActive) return;
        if (activeCarouselCard) {
            const shelf = activeCarouselCard.closest('.reels-carousel-shelf');
            if (shelf) {
                const r = shelf.getBoundingClientRect();
                const vh = window.innerHeight || document.documentElement.clientHeight;
                if (r.bottom < 60 || r.top > vh - 60) {
                    stopCarouselAutoplay();
                }
            }
        }
        const visibleShelf = document.querySelector('.reels-carousel-shelf[data-in-view="true"]');
        if (visibleShelf) {
            scheduleCarouselStickCheck(visibleShelf, 400);
        }
    }, { passive: true });

    function initializeVideos(container = document.body) {
        if (!container) container = document.body;
        if (!state.feedObserver) {
            createFeedObserver();
        }

        const videos = [];
        if (container.tagName === 'VIDEO') {
            videos.push(container);
        } else if (container.querySelectorAll) {
            videos.push(...container.querySelectorAll('video'));
        }

        videos.forEach(video => {
            if (isMessagingVideo(video)) {
                video.autoplay = false;
                video.removeAttribute('autoplay');
                try { video.pause(); } catch (_) {}
                return;
            }

            // Attach buffering indicator and micro-scrubber listeners across feed & fullscreen
            const cardContainer = video.closest('.reel-card-container, .reel-post-card, .reel-stage-container, .reels-snap-item, .media-video-container, .landscape-video-container, .landscape-video-wrapper, .post-card, .post-media-wrapper');
            const postId = extractPostId(video) || (cardContainer ? extractPostId(cardContainer) : null);
            const spinner = cardContainer ? (cardContainer.querySelector('.reel-buffering-indicator, .video-buffering-indicator') || (postId ? document.getElementById(`reel-buffer-spinner-${postId}`) || document.getElementById(`video-buffer-spinner-${postId}`) : null)) : null;
            const progressBar = cardContainer ? cardContainer.querySelector('.reel-progress-container') : null;
            const vinyl = cardContainer ? cardContainer.querySelector('.reel-vinyl-disc') : null;
            wireVideoBufferingListeners(video, cardContainer, spinner, progressBar, vinyl);

            if (!video.dataset.pwaniObserved) {
                video.dataset.pwaniObserved = 'true';
                isProgrammaticAudioSync = true;
                video.muted = isGlobalMuted;
                setTimeout(() => { isProgrammaticAudioSync = false; }, 80);

                // Sync global mute if user changes volume / unmuted on native controls
                video.addEventListener('volumechange', () => {
                    if (isProgrammaticAudioSync) return;
                    if (video._isAutoplayMutedFallback) return;
                    if (video.classList.contains('reels-carousel-video') || video.closest('.reels-carousel-shelf')) return;
                    if (video.muted !== isGlobalMuted) {
                        setGlobalMute(video.muted);
                    }
                });

                // When paused, ensure play HUD is shown
                video.addEventListener('pause', () => {
                    video.classList.remove('is-playing');
                    if (cardContainer) cardContainer.classList.remove('is-video-playing');
                    if (!video.seeking && !video.ended) {
                        triggerPlayHud(video, false);
                    }
                });
                video.addEventListener('playing', () => {
                    video.classList.add('is-playing');
                    if (cardContainer) cardContainer.classList.add('is-video-playing');
                    triggerPlayHud(video, true);
                });
                video.addEventListener('play', () => {
                    video.classList.add('is-playing');
                    if (cardContainer) cardContainer.classList.add('is-video-playing');
                    triggerPlayHud(video, true);
                });

                // Carousel videos are controlled exclusively by the carousel stick-autoplay engine
                if (video.classList.contains('reels-carousel-video') || video.closest('.reels-carousel-shelf')) {
                    video.dataset.autoplay = 'false';
                    return;
                }

                // For videos that strictly do not autoplay (manual play only), show Play HUD when paused
                if (video.dataset.autoplay === 'false') {
                    if (video.paused) {
                        video.classList.remove('is-playing');
                        if (cardContainer) cardContainer.classList.remove('is-video-playing');
                        triggerPlayHud(video, false);
                    } else {
                        video.classList.add('is-playing');
                        if (cardContainer) cardContainer.classList.add('is-video-playing');
                        triggerPlayHud(video, true);
                    }
                } else {
                    // Autoplay videos: keep Play HUD hidden so it never flashes while loading/scrolling into view
                    triggerPlayHud(video, true);
                    if (!video.paused) {
                        video.classList.add('is-playing');
                        if (cardContainer) cardContainer.classList.add('is-video-playing');
                    }
                }

                // Feed observer tracks all videos (reels and postcards) for 65% visibility autoplay
                if (state.feedObserver) {
                    state.feedObserver.observe(video);
                }
                checkAndPromoteLegacyVideo(video);
            } else if (state.feedObserver && !video.classList.contains('reels-carousel-video') && !video.closest('.reels-carousel-shelf')) {
                try {
                    state.feedObserver.observe(video);
                } catch (_) {}
            }
        });

        // Initialize carousel shelves inside this container
        initializeCarouselShelves(container);

        syncMuteButtons();
    }

    function setupMutationObserver() {
        if (state.mutationObserver) {
            state.mutationObserver.disconnect();
        }

        const htmxSelector = '[hx-get], [hx-post], [hx-put], [hx-patch], [hx-delete], [hx-boost], [hx-trigger]';

        state.mutationObserver = new MutationObserver(mutations => {
            let hasNewVideos = false;
            const htmxRoots = new Set();

            mutations.forEach(mutation => {
                mutation.addedNodes.forEach(node => {
                    if (node.nodeType === Node.ELEMENT_NODE) {
                        // Ignore mutations occurring inside fullscreen reels viewport or messaging containers to avoid document-wide re-scans
                        if (node.closest && (
                            node.closest('#reelsSnapViewport') || 
                            node.closest('#fullscreenReelsOverlay') ||
                            node.closest('#messagesContainer') ||
                            node.closest('.messages-area') ||
                            node.closest('.media-bubble') ||
                            node.closest('#chatMessages') ||
                            node.closest('#chatAppContainer')
                        )) {
                            return;
                        }
                        if (node.id === 'reelsSnapViewport' || node.id === 'fullscreenReelsOverlay' || node.id === 'messagesContainer' || node.id === 'chatMessages') {
                            return;
                        }
                        if (node.classList && (node.classList.contains('media-bubble') || node.classList.contains('message-wrapper') || node.classList.contains('messages-area'))) {
                            return;
                        }
                        if (node.tagName === 'VIDEO' || (node.querySelector && node.querySelector('video'))) {
                            hasNewVideos = true;
                        }
                        if (window.htmx) {
                            if ((node.matches && node.matches(htmxSelector)) || (node.querySelector && node.querySelector(htmxSelector))) {
                                htmxRoots.add(node);
                            }
                        }
                    }
                });
            });

            if (htmxRoots.size > 0 && window.htmx) {
                htmxRoots.forEach(root => {
                    try {
                        window.htmx.process(root);
                    } catch (_) {}
                });
            }

            if (hasNewVideos && !state.isFullScreenActive) {
                initializeVideos(document.body);
            }
        });

        state.mutationObserver.observe(document.body, {
            childList: true,
            subtree: true
        });
    }

    function initHTMXIntegration() {
        document.addEventListener('htmx:beforeSwap', function(event) {
            const target = event.detail?.target;
            if (state.isFullScreenActive && target) {
                const isPageNavigation = target.id === 'page-content-target' ||
                                         target.tagName === 'BODY' ||
                                         target.tagName === 'MAIN';
                if (isPageNavigation) {
                    closeFullscreenReels();
                }
            }
            if (target && target.querySelectorAll) {
                const oldVideos = target.querySelectorAll('video');
                oldVideos.forEach(v => cleanupVideo(v));
            }
        });

        document.addEventListener('htmx:beforeCleanupElement', function(event) {
            const elt = event.target;
            if (elt && elt.querySelectorAll) {
                const oldVideos = elt.querySelectorAll('video');
                oldVideos.forEach(v => cleanupVideo(v));
            }
        });

        document.addEventListener('htmx:afterSwap', function(event) {
            const target = event.detail?.target;
            if (target) {
                initializeVideos(target);
                if (target.closest && (target.closest('#fsRailCommentsList') || target.closest('#commentsModalList') || target.id === 'comments-section')) {
                    truncateLongComments(target);
                }
                if (state.isFullScreenActive) {
                    appendNewVideosToFullscreenViewport();
                    const activeSnap = getActiveSnapItem();
                    if (activeSnap) {
                        adaptSnapItemOrientation(activeSnap);
                    }
                }
            }
        });

        document.addEventListener('htmx:load', function(event) {
            const elt = event.detail?.elt || document.body;
            if (elt) {
                initializeVideos(elt);
                if (state.isFullScreenActive) {
                    appendNewVideosToFullscreenViewport();
                    const activeSnap = getActiveSnapItem();
                    if (activeSnap) {
                        adaptSnapItemOrientation(activeSnap);
                    }
                }
            }
            checkAutoLaunchReel();
        });

        document.addEventListener('htmx:historyRestore', function() {
            reinitHtmxElement(document.body);
            initializeVideos(document.body);
        });
    }

    function init() {
        if (state.isInitialized) return;

        createFeedObserver();
        setupDelegatedListeners();
        setupMutationObserver();
        initHTMXIntegration();
        initializeVideos(document.body);

        const railCommentsList = document.getElementById('fsRailCommentsList');
        if (railCommentsList) {
            initSheetCommentsInteractions(railCommentsList, '');
        }
        const modalCommentsList = document.getElementById('commentsModalList');
        if (modalCommentsList) {
            initSheetCommentsInteractions(modalCommentsList, '');
        }

        checkAutoLaunchReel();

        state.isInitialized = true;
        console.log('[VideoManager] Unified video playback engine initialized');
    }

    // ============================================================================
    // PUBLIC API
    // ============================================================================

    window.openCommentsSheet = openCommentsSheet;
    window.openReelComments = openCommentsSheet;

    window.vastoVideoManager = {
        init,
        playVideo,
        pauseVideo,
        pauseAllVideos,
        cleanupVideo,
        setGlobalMute,
        toggleGlobalMute,
        setAutoScroll,
        toggleAutoScroll,
        syncAutoScrollUI,
        checkAndPromoteLegacyVideo,
        openFullscreenReels,
        closeFullscreenReels,
        openCommentsSheet,
        openReelComments,
        toggleReelLike,
        toggleReelRepost,
        syncLikeUiAcrossSite,
        syncRepostUiAcrossSite,
        showSharedByNote,
        get isGlobalMuted() { return isGlobalMuted; },
        get isAutoScrollEnabled() { return isAutoScrollEnabled; },
        get state() { return state; },
        get reelSlotEngine() { return reelSlotEngine; },
        updateThreeSlotWindow,
        evictSnapItemMedia,
        warmSnapItemMedia
    };

    window.videoManager = window.vastoVideoManager;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
