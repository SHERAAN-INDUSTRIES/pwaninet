/**
 * Universal Media Picker (Emoji, GIFs, Stickers)
 * Supports Mobile Bottom Sheet & Desktop/Tablet Floating Anchored Model
 * Integrates with KLIPY for GIFs & Stickers + Built-in offline packs
 */

(function(window, document) {
  'use strict';

  const BUILTIN_STICKERS = [
    { name: 'Fire', file: 'fire.svg' },
    { name: 'Heart Eyes', file: 'heart_eyes.svg' },
    { name: 'Party', file: 'party.svg' },
    { name: 'Laughing', file: 'laughing.svg' },
    { name: 'Thumbs Up', file: 'thumbs_up.svg' },
    { name: 'Rocket', file: 'rocket.svg' },
    { name: '100', file: '100.svg' },
    { name: 'Cool', file: 'cool.svg' },
    { name: 'Clap', file: 'clap.svg' },
    { name: 'Mind Blown', file: 'mind_blown.svg' },
    { name: 'Crying', file: 'crying.svg' },
    { name: 'Wow', file: 'wow.svg' },
    { name: 'Hug', file: 'hug.svg' },
    { name: 'Peace', file: 'peace.svg' },
    { name: 'Dance', file: 'dance.svg' },
    { name: 'Thinking', file: 'thinking.svg' }
  ];

  const QUICK_EMOJIS = ['😂', '❤️', '🔥', '👏', '😢', '😮', '🙏', '💯', '😍', '🎉'];

  const MediaPicker = {
    isOpen: false,
    activeTab: 'emojis',
    activeStickerSubtab: 'klipy',
    currentTargetInput: null,
    currentForm: null,
    currentTrigger: null,
    gifSearchTimeout: null,
    stickerSearchTimeout: null,
    gifPage: 1,
    stickerPage: 1,
    hasMoreGifs: true,
    hasMoreStickers: true,
    isLoadingGifs: false,
    isLoadingStickers: false,
    gifQuery: '',
    stickerQuery: '',
    gifRequestId: 0,
    stickerRequestId: 0,
    _myStickersCache: null,

    escapeHtml(text) {
      if (!text) return '';
      const div = document.createElement('div');
      div.textContent = String(text);
      return div.innerHTML;
    },

    init() {
      if (this._initialized) return;
      this._initialized = true;

      this.buildDOM();
      this.bindGlobalEvents();
      this.initEmojiPickerElement();
      this.initPasteSupport();
    },

    buildDOM() {
      if (document.getElementById('mediaPickerModal')) return;

      // Backdrop
      const backdrop = document.createElement('div');
      backdrop.id = 'mediaPickerBackdrop';
      backdrop.className = 'media-picker-backdrop';

      // Main Modal Container
      const modal = document.createElement('div');
      modal.id = 'mediaPickerModal';
      modal.className = 'media-picker-modal';
      modal.setAttribute('role', 'dialog');
      modal.setAttribute('aria-modal', 'true');
      modal.setAttribute('aria-label', 'Emoji, GIF and Sticker Picker');

      modal.innerHTML = `
        <div class="media-picker-drag-handle" aria-hidden="true"></div>
        <div class="media-picker-header">
          <div class="media-picker-tabs" role="tablist">
            <button type="button" class="media-picker-tab-btn active" data-tab="emojis" role="tab" aria-selected="true">
              <i aria-hidden="true" data-lucide="smile" class="vasto-icon"></i> Emoji
            </button>
            <button type="button" class="media-picker-tab-btn" data-tab="gifs" role="tab" aria-selected="false">
              <i aria-hidden="true" data-lucide="video" class="vasto-icon"></i> GIFs
            </button>
            <button type="button" class="media-picker-tab-btn" data-tab="stickers" role="tab" aria-selected="false">
              <i aria-hidden="true" data-lucide="sticky-note" class="vasto-icon"></i> Stickers
            </button>
          </div>
          <button type="button" class="media-picker-close-btn" id="mediaPickerCloseBtn" aria-label="Close picker">
            <i aria-hidden="true" data-lucide="x" class="vasto-icon"></i>
          </button>
        </div>

        <!-- Quick Reactions Bar -->
        <div class="media-picker-quick-bar" id="mediaPickerQuickBar">
          ${QUICK_EMOJIS.map(em => `<button type="button" class="media-picker-quick-emoji" data-emoji="${em}" title="${em}">${em}</button>`).join('')}
        </div>

        <div class="media-picker-body">
          <!-- EMOJI PANEL -->
          <div class="media-picker-panel active" id="mediaPickerEmojiPanel" data-panel="emojis">
            <div class="media-picker-emoji-container" id="mediaPickerEmojiContainer">
              <!-- <emoji-picker> element injected here -->
            </div>
          </div>

          <!-- GIFS PANEL -->
          <div class="media-picker-panel" id="mediaPickerGifPanel" data-panel="gifs">
            <div class="media-picker-search-wrap">
              <i aria-hidden="true" data-lucide="search" class="vasto-icon text-muted position-absolute" style="left: 24px;"></i>
              <input type="text" class="media-picker-search-input" id="mediaPickerGifSearch" placeholder="Search GIFs with KLIPY..." autocomplete="off">
              <button type="button" class="media-picker-search-clear" id="mediaPickerGifClear" title="Clear search"><i aria-hidden="true" data-lucide="circle-x" class="vasto-icon"></i></button>
            </div>
            <div class="media-picker-categories" id="mediaPickerGifCategories">
              <button type="button" class="media-picker-chip active" data-query="">🔥 Trending</button>
              <button type="button" class="media-picker-chip" data-query="lol">😂 LOL</button>
              <button type="button" class="media-picker-chip" data-query="love">❤️ Love</button>
              <button type="button" class="media-picker-chip" data-query="party">🎉 Party</button>
              <button type="button" class="media-picker-chip" data-query="clap">👏 Clap</button>
              <button type="button" class="media-picker-chip" data-query="cats">🐱 Cats</button>
              <button type="button" class="media-picker-chip" data-query="dance">💃 Dance</button>
              <button type="button" class="media-picker-chip" data-query="sad">😢 Sad</button>
              <button type="button" class="media-picker-chip" data-query="yes">👍 Yes</button>
              <button type="button" class="media-picker-chip" data-query="no">🤦 No</button>
              <button type="button" class="media-picker-chip" data-query="wow">😮 Wow</button>
            </div>
            <div class="media-picker-grid" id="mediaPickerGifGrid">
              <div class="media-picker-loading"><div class="spinner-border spinner-border-sm text-primary"></div><span>Loading GIFs...</span></div>
            </div>
            <div class="media-picker-footer">
              <span class="text-muted small">Powered by</span>
              <a href="https://klipy.com" target="_blank" rel="noopener" class="media-picker-klipy-brand">
                <img src="/static/images/klipy-logo.png" alt="KLIPY">
              </a>
            </div>
          </div>

          <!-- STICKERS PANEL -->
          <div class="media-picker-panel" id="mediaPickerStickerPanel" data-panel="stickers">
            <div class="media-picker-subtabs">
              <button type="button" class="media-picker-pill active" data-subtab="klipy">KLIPY Stickers</button>
              <button type="button" class="media-picker-pill" data-subtab="builtin">Built-in Pack</button>
              <button type="button" class="media-picker-pill" data-subtab="mine">My Stickers</button>
            </div>
            <div class="media-picker-search-wrap" id="mediaPickerStickerSearchWrap">
              <i aria-hidden="true" data-lucide="search" class="vasto-icon text-muted position-absolute" style="left: 24px;"></i>
              <input type="text" class="media-picker-search-input" id="mediaPickerStickerSearch" placeholder="Search stickers with KLIPY..." autocomplete="off">
              <button type="button" class="media-picker-search-clear" id="mediaPickerStickerClear" title="Clear search"><i aria-hidden="true" data-lucide="circle-x" class="vasto-icon"></i></button>
            </div>
            <div class="media-picker-categories" id="mediaPickerStickerCategories">
              <button type="button" class="media-picker-chip active" data-query="">🔥 Trending</button>
              <button type="button" class="media-picker-chip" data-query="love">❤️ Love</button>
              <button type="button" class="media-picker-chip" data-query="cute">✨ Cute</button>
              <button type="button" class="media-picker-chip" data-query="funny">😂 Funny</button>
              <button type="button" class="media-picker-chip" data-query="party">🎉 Party</button>
              <button type="button" class="media-picker-chip" data-query="animals">🐱 Animals</button>
              <button type="button" class="media-picker-chip" data-query="anime">💥 Anime</button>
              <button type="button" class="media-picker-chip" data-query="cool">😎 Cool</button>
              <button type="button" class="media-picker-chip" data-query="mood">💬 Mood</button>
            </div>
            <div class="media-picker-grid stickers-grid" id="mediaPickerStickerGrid">
              <div class="media-picker-loading"><div class="spinner-border spinner-border-sm text-primary"></div><span>Loading stickers...</span></div>
            </div>
            <div class="media-picker-footer" id="mediaPickerStickerFooter">
              <span class="text-muted small">Powered by</span>
              <a href="https://klipy.com" target="_blank" rel="noopener" class="media-picker-klipy-brand">
                <img src="/static/images/klipy-logo.png" alt="KLIPY">
              </a>
            </div>
          </div>
        </div>

        <input type="file" id="mediaPickerCustomStickerInput" accept="image/*" style="display: none;">
      `;

      // Stop clicks inside picker modal from bubbling and dismissing parent modals
      modal.addEventListener('click', (e) => {
        e.stopPropagation();
      });

      document.body.appendChild(backdrop);
      document.body.appendChild(modal);

      this.backdropEl = backdrop;
      this.modalEl = modal;
    },

    initEmojiPickerElement() {
      const container = document.getElementById('mediaPickerEmojiContainer');
      if (!container) return;

      // Lazy load emoji-picker custom element module
      if (!window.customElements.get('emoji-picker')) {
        import('/static/vendor/emoji-picker/index.js')
          .then(() => {
            this.mountEmojiPickerTag(container);
          })
          .catch(err => {
            console.warn('[MediaPicker] Could not import local emoji-picker module, trying fallback:', err);
            // Fallback: create dynamic script
            const s = document.createElement('script');
            s.type = 'module';
            s.src = '/static/vendor/emoji-picker/index.js';
            s.onload = () => this.mountEmojiPickerTag(container);
            document.head.appendChild(s);
          });
      } else {
        this.mountEmojiPickerTag(container);
      }
    },

    mountEmojiPickerTag(container) {
      if (container.querySelector('emoji-picker')) return;

      const picker = document.createElement('emoji-picker');
      picker.setAttribute('data-source', '/static/vendor/emoji-picker/data/emojibase-en.json');
      
      // Inherit dark mode if active and observe theme changes
      const syncTheme = () => {
        const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        picker.classList.toggle('dark', isDark);
      };
      syncTheme();
      try {
        const observer = new MutationObserver(syncTheme);
        observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
      } catch (_) {}

      picker.addEventListener('emoji-click', (e) => {
        if (e.stopPropagation) e.stopPropagation();
        const unicode = e.detail?.unicode || e.detail?.emoji?.unicode;
        if (unicode) {
          this.insertEmoji(unicode);
        }
      });

      picker.addEventListener('skin-tone-change', (e) => {
        // persists natively in indexdb
      });

      container.appendChild(picker);
    },

    bindGlobalEvents() {
      // 1. Delegated click for all emoji / media triggers across the page
      document.addEventListener('click', (e) => {
        const trigger = e.target.closest('[data-media-picker], [data-action="emoji"], .composer-media-trigger, .comment-emoji-btn');
        if (trigger) {
          e.preventDefault();
          e.stopPropagation();
          this.toggle(trigger);
          return;
        }

        // Close when clicking outside of modal & not on trigger
        if (this.isOpen && this.modalEl && !this.modalEl.contains(e.target) && !e.target.closest('[data-media-picker], [data-action="emoji"], .composer-media-trigger')) {
          this.close();
        }
      });

      // 2. Backdrop click
      if (this.backdropEl) {
        this.backdropEl.addEventListener('click', () => this.close());
      }

      // 3. Close button
      const closeBtn = document.getElementById('mediaPickerCloseBtn');
      if (closeBtn) {
        closeBtn.addEventListener('click', () => this.close());
      }

      // 4. Quick reactions
      const quickBar = document.getElementById('mediaPickerQuickBar');
      if (quickBar) {
        quickBar.addEventListener('click', (e) => {
          e.stopPropagation();
          const btn = e.target.closest('.media-picker-quick-emoji');
          if (btn && btn.dataset.emoji) {
            this.insertEmoji(btn.dataset.emoji);
          }
        });
      }

      // 5. Tabs
      const tabs = document.querySelectorAll('.media-picker-tab-btn');
      tabs.forEach(tab => {
        tab.addEventListener('click', (e) => {
          e.preventDefault();
          this.switchTab(tab.dataset.tab);
        });
      });

      // 6. Sticker Subtabs
      const subtabs = document.querySelectorAll('.media-picker-pill');
      subtabs.forEach(st => {
        st.addEventListener('click', (e) => {
          e.preventDefault();
          this.switchStickerSubtab(st.dataset.subtab);
        });
      });

      // 7. GIF Search with debounce & category chips
      const gifSearch = document.getElementById('mediaPickerGifSearch');
      const gifClear = document.getElementById('mediaPickerGifClear');
      const gifCategories = document.getElementById('mediaPickerGifCategories');

      const syncGifCategoryPills = (query) => {
        if (!gifCategories) return;
        const qNorm = (query || '').toLowerCase().trim();
        gifCategories.querySelectorAll('.media-picker-chip').forEach(chip => {
          const chipQ = (chip.dataset.query || '').toLowerCase().trim();
          chip.classList.toggle('active', chipQ === qNorm);
        });
      };

      if (gifCategories) {
        gifCategories.addEventListener('click', (e) => {
          const chip = e.target.closest('.media-picker-chip');
          if (!chip) return;
          e.preventDefault();
          const q = chip.dataset.query || '';
          if (gifSearch) gifSearch.value = q;
          if (gifClear) gifClear.classList.toggle('show', q.length > 0);
          syncGifCategoryPills(q);
          clearTimeout(this.gifSearchTimeout);
          this.gifQuery = q;
          this.gifPage = 1;
          this.loadGifs(true);
        });
      }

      if (gifSearch) {
        gifSearch.addEventListener('input', (e) => {
          const val = e.target.value.trim();
          gifClear.classList.toggle('show', val.length > 0);
          syncGifCategoryPills(val);
          clearTimeout(this.gifSearchTimeout);
          this.gifSearchTimeout = setTimeout(() => {
            this.gifQuery = val;
            this.gifPage = 1;
            this.loadGifs(true);
          }, 280);
        });

        gifSearch.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            clearTimeout(this.gifSearchTimeout);
            const val = gifSearch.value.trim();
            this.gifQuery = val;
            this.gifPage = 1;
            this.loadGifs(true);
          }
        });
      }

      if (gifClear) {
        gifClear.addEventListener('click', () => {
          if (gifSearch) gifSearch.value = '';
          gifClear.classList.remove('show');
          syncGifCategoryPills('');
          clearTimeout(this.gifSearchTimeout);
          this.gifQuery = '';
          this.gifPage = 1;
          this.loadGifs(true);
        });
      }

      // 8. Sticker Search with debounce & category chips
      const stickerSearch = document.getElementById('mediaPickerStickerSearch');
      const stickerClear = document.getElementById('mediaPickerStickerClear');
      const stickerCategories = document.getElementById('mediaPickerStickerCategories');

      const syncStickerCategoryPills = (query) => {
        if (!stickerCategories) return;
        const qNorm = (query || '').toLowerCase().trim();
        stickerCategories.querySelectorAll('.media-picker-chip').forEach(chip => {
          const chipQ = (chip.dataset.query || '').toLowerCase().trim();
          chip.classList.toggle('active', chipQ === qNorm);
        });
      };

      if (stickerCategories) {
        stickerCategories.addEventListener('click', (e) => {
          const chip = e.target.closest('.media-picker-chip');
          if (!chip) return;
          e.preventDefault();
          if (this.activeStickerSubtab !== 'klipy') {
            this.switchStickerSubtab('klipy');
          }
          const q = chip.dataset.query || '';
          if (stickerSearch) stickerSearch.value = q;
          if (stickerClear) stickerClear.classList.toggle('show', q.length > 0);
          syncStickerCategoryPills(q);
          clearTimeout(this.stickerSearchTimeout);
          this.stickerQuery = q;
          this.stickerPage = 1;
          this.loadStickers(true);
        });
      }

      if (stickerSearch) {
        stickerSearch.addEventListener('input', (e) => {
          const val = e.target.value.trim();
          stickerClear.classList.toggle('show', val.length > 0);
          syncStickerCategoryPills(val);
          clearTimeout(this.stickerSearchTimeout);

          if (this.activeStickerSubtab === 'builtin') {
            this.renderBuiltinStickers(val);
          } else if (this.activeStickerSubtab === 'mine') {
            this.loadMyStickers(val);
          } else {
            this.stickerSearchTimeout = setTimeout(() => {
              this.stickerQuery = val;
              this.stickerPage = 1;
              this.loadStickers(true);
            }, 280);
          }
        });

        stickerSearch.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            clearTimeout(this.stickerSearchTimeout);
            const val = stickerSearch.value.trim();
            this.stickerQuery = val;
            this.stickerPage = 1;
            this.loadStickers(true);
          }
        });
      }

      if (stickerClear) {
        stickerClear.addEventListener('click', () => {
          if (stickerSearch) stickerSearch.value = '';
          stickerClear.classList.remove('show');
          syncStickerCategoryPills('');
          clearTimeout(this.stickerSearchTimeout);
          this.stickerQuery = '';
          this.stickerPage = 1;
          this.loadStickers(true);
        });
      }

      // 9. Infinite Scroll on Grids
      const gifGrid = document.getElementById('mediaPickerGifGrid');
      if (gifGrid) {
        gifGrid.addEventListener('scroll', () => {
          if (this.isLoadingGifs || !this.hasMoreGifs) return;
          if (gifGrid.scrollTop + gifGrid.clientHeight >= gifGrid.scrollHeight - 140) {
            this.gifPage++;
            this.loadGifs(false);
          }
        }, { passive: true });
      }

      const stickerGrid = document.getElementById('mediaPickerStickerGrid');
      if (stickerGrid) {
        stickerGrid.addEventListener('scroll', () => {
          if (this.activeStickerSubtab !== 'klipy') return;
          if (this.isLoadingStickers || !this.hasMoreStickers) return;
          if (stickerGrid.scrollTop + stickerGrid.clientHeight >= stickerGrid.scrollHeight - 140) {
            this.stickerPage++;
            this.loadStickers(false);
          }
        }, { passive: true });
      }

      // 10. Action buttons in empty states & errors
      if (this.modalEl) {
        this.modalEl.addEventListener('click', (e) => {
          const clearGif = e.target.closest('[data-action="clear-gif-search"]');
          if (clearGif && gifClear) {
            e.preventDefault();
            gifClear.click();
            return;
          }

          const clearSticker = e.target.closest('[data-action="clear-sticker-search"]');
          if (clearSticker && stickerClear) {
            e.preventDefault();
            stickerClear.click();
            return;
          }

          const switchKlipy = e.target.closest('[data-action="switch-to-klipy-stickers"]');
          if (switchKlipy) {
            e.preventDefault();
            this.switchStickerSubtab('klipy');
            return;
          }

          const switchBuiltin = e.target.closest('[data-action="switch-to-builtin-stickers"]');
          if (switchBuiltin) {
            e.preventDefault();
            this.switchStickerSubtab('builtin');
            return;
          }

          const retryGif = e.target.closest('[data-action="retry-gif-search"]');
          if (retryGif) {
            e.preventDefault();
            this.loadGifs(true);
            return;
          }

          const retrySticker = e.target.closest('[data-action="retry-sticker-search"]');
          if (retrySticker) {
            e.preventDefault();
            this.loadStickers(true);
            return;
          }
        });
      }

      // 11. ESC key to close
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && this.isOpen) {
          this.close();
        }
      });

      // 12. Window resize / scroll position update
      window.addEventListener('resize', () => {
        if (this.isOpen && window.innerWidth >= 768 && this.currentTrigger) {
          this.positionModal(this.currentTrigger);
        }
      });

      // 13. Custom sticker file input
      const customStickerInput = document.getElementById('mediaPickerCustomStickerInput');
      if (customStickerInput) {
        customStickerInput.addEventListener('change', (e) => {
          const file = e.target.files && e.target.files[0];
          if (file) {
            this.uploadCustomSticker(file);
          }
        });
      }

      // 14. Delegate image / camera attachment buttons: [data-action="image-comment"]
      document.addEventListener('change', (e) => {
        if (e.target.matches('input[type="file"].comment-photo-input')) {
          const file = e.target.files && e.target.files[0];
          if (file) {
            const form = e.target.closest('form') || e.target.closest('.comment-composer');
            this.attachLocalImage(file, form);
          }
        }
      });

      // 15. Delegate remove attachment chip button
      document.addEventListener('click', (e) => {
        const removeBtn = e.target.closest('.comment-attachment-remove-btn');
        if (removeBtn) {
          e.preventDefault();
          const form = removeBtn.closest('form') || removeBtn.closest('.comment-composer') || document;
          this.clearAttachment(form);
        }
      });

      // 16. Images committed by the Android keyboard (Gboard screenshot chip / clipboard)
      //     are delivered by the native vastoWebView as a window event.
      document.addEventListener('focusin', (e) => {
        const t = e.target;
        if (t && t.closest && t.closest('.composer-input-editable, textarea, input[type="text"]')) {
          const f = t.closest('form') || t.closest('.comment-composer');
          if (f) this._lastFocusedComposerForm = f;
        }
      });
      window.addEventListener('vasto:native-image-paste', (e) => {
        const detail = e.detail || {};
        if (!detail.data) return;
        try {
          const mime = detail.mimeType || 'image/png';
          const bin = atob(detail.data);
          const bytes = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
          const ext = ((mime.split('/')[1] || 'png').split('+')[0]).replace('jpeg', 'jpg');
          const file = new File([bytes], `keyboard_${Date.now()}.${ext}`, { type: mime });
          const active = document.activeElement;
          const form = (active && active.closest && (active.closest('form') || active.closest('.comment-composer'))) ||
                       this._lastFocusedComposerForm || this.currentForm ||
                       document.querySelector('#reelCommentForm, #commentForm, #fsRailCommentForm, .comment-composer, .main-composer');
          if (form) this.attachLocalImage(file, form);
        } catch (err) {
          console.warn('[MediaPicker] Native keyboard image failed:', err);
        }
      });

      // 17. Intercept paste event globally on comment forms and editable composers (both desktop Ctrl+V and Gboard screenshot commits)
      document.addEventListener('paste', (e) => {
        const target = e.target;
        const composer = target.closest('form, .comment-composer, .modal-footer, #commentForm, #reelCommentForm, #fsRailCommentForm, .main-composer, .post-detail-comments-container');
        if (!composer) return;

        const clipboardData = e.clipboardData || window.clipboardData;
        if (!clipboardData) return;

        let imageFile = null;

        // Check clipboard files
        if (clipboardData.files && clipboardData.files.length > 0) {
          for (let i = 0; i < clipboardData.files.length; i++) {
            const f = clipboardData.files[i];
            if (f.type && f.type.startsWith('image/')) {
              imageFile = f;
              break;
            }
          }
        }

        // Check clipboard items (standard for Gboard CommitContent and browser image copying)
        if (!imageFile && clipboardData.items && clipboardData.items.length > 0) {
          for (let i = 0; i < clipboardData.items.length; i++) {
            const item = clipboardData.items[i];
            if (item.kind === 'file' && item.type && item.type.startsWith('image/')) {
              imageFile = item.getAsFile();
              break;
            }
          }
        }

        if (imageFile) {
          e.preventDefault();
          e.stopPropagation();
          const form = composer.closest('form') || composer.querySelector('form') || composer;
          this.attachLocalImage(imageFile, form);
        }
      }, true);
    },

    toggle(triggerEl) {
      if (this.isOpen && this.currentTrigger === triggerEl) {
        this.close();
      } else {
        this.open(triggerEl);
      }
    },

    open(triggerEl) {
      this.init();
      this.currentTrigger = triggerEl;

      // Locate target input & form
      const targetSelector = triggerEl.dataset.input;
      let targetInput = targetSelector ? document.querySelector(targetSelector) : null;
      let form = triggerEl.closest('form') || triggerEl.closest('.comment-composer');

      if (!targetInput && form) {
        targetInput = form.querySelector('input[type="text"], textarea');
      } else if (!targetInput) {
        const container = triggerEl.closest('.comment-composer, .main-composer-input-wrapper, .modal-footer, .comment-row');
        if (container) {
          targetInput = container.querySelector('input[type="text"], textarea');
          form = container.querySelector('form') || container.closest('.comment-composer') || form;
        }
      }

      this.currentTargetInput = targetInput;
      this.currentForm = form;

      // Position modal
      this.positionModal(triggerEl);

      // Show modal
      this.backdropEl.classList.add('show');
      this.modalEl.classList.add('show');
      this.isOpen = true;

      // Sync dark mode class on emoji picker
      const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
      const picker = this.modalEl.querySelector('emoji-picker');
      if (picker) {
        picker.classList.toggle('dark', isDark);
      }

      // Default load GIFs / Stickers if needed
      if (this.activeTab === 'gifs' && !this._gifsLoaded) {
        this.loadGifs();
      } else if (this.activeTab === 'stickers' && !this._stickersLoaded) {
        this.loadStickers();
      }
    },

    positionModal(triggerEl) {
      if (window.innerWidth < 768) {
        // Mobile bottom sheet: clear inline coordinates
        this.modalEl.style.left = '';
        this.modalEl.style.right = '';
        this.modalEl.style.top = '';
        this.modalEl.style.bottom = '';
        return;
      }

      // Desktop / Tablet floating anchored popover
      const rect = triggerEl.getBoundingClientRect();
      const modalWidth = 370;
      const modalHeight = 460;
      const padding = 12;

      // Horizontal: align with trigger, but keep inside viewport
      let left = rect.left + (rect.width / 2) - (modalWidth / 2);
      if (left < padding) left = padding;
      if (left + modalWidth > window.innerWidth - padding) {
        left = window.innerWidth - modalWidth - padding;
      }

      // Vertical: prefer above the trigger, flip below if not enough space
      let top;
      if (rect.top - modalHeight - 8 >= padding) {
        top = rect.top - modalHeight - 8;
      } else if (rect.bottom + modalHeight + 8 <= window.innerHeight - padding) {
        top = rect.bottom + 8;
      } else {
        // Center vertically if neither fits
        top = Math.max(padding, (window.innerHeight - modalHeight) / 2);
      }

      this.modalEl.style.left = `${Math.round(left)}px`;
      this.modalEl.style.top = `${Math.round(top)}px`;
      this.modalEl.style.bottom = 'auto';
      this.modalEl.style.right = 'auto';
    },

    close() {
      if (!this.isOpen) return;
      this.isOpen = false;
      this.currentTrigger = null;

      if (this.modalEl) this.modalEl.classList.remove('show');
      if (this.backdropEl) this.backdropEl.classList.remove('show');
    },

    switchTab(tabName) {
      this.activeTab = tabName;

      document.querySelectorAll('.media-picker-tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
        btn.setAttribute('aria-selected', btn.dataset.tab === tabName ? 'true' : 'false');
      });

      document.querySelectorAll('.media-picker-panel').forEach(panel => {
        panel.classList.toggle('active', panel.dataset.panel === tabName);
      });

      // Quick bar is only visible for Emoji tab
      const quickBar = document.getElementById('mediaPickerQuickBar');
      if (quickBar) {
        quickBar.style.display = tabName === 'emojis' ? 'flex' : 'none';
      }

      if (tabName === 'gifs' && !this._gifsLoaded) {
        this.loadGifs(true);
      } else if (tabName === 'stickers' && !this._stickersLoaded) {
        this.loadStickers(true);
      }
    },

    switchStickerSubtab(subtabName) {
      this.activeStickerSubtab = subtabName;

      document.querySelectorAll('.media-picker-pill').forEach(pill => {
        pill.classList.toggle('active', pill.dataset.subtab === subtabName);
      });

      const searchInput = document.getElementById('mediaPickerStickerSearch');
      const searchWrap = document.getElementById('mediaPickerStickerSearchWrap');
      const catBar = document.getElementById('mediaPickerStickerCategories');
      const footer = document.getElementById('mediaPickerStickerFooter');

      if (subtabName === 'builtin') {
        if (searchWrap) searchWrap.style.display = 'flex';
        if (searchInput) searchInput.placeholder = 'Filter built-in stickers...';
        if (catBar) catBar.style.display = 'none';
        if (footer) footer.style.display = 'none';
        this.renderBuiltinStickers(searchInput ? searchInput.value.trim() : '');
      } else if (subtabName === 'mine') {
        if (searchWrap) searchWrap.style.display = 'flex';
        if (searchInput) searchInput.placeholder = 'Filter your stickers...';
        if (catBar) catBar.style.display = 'none';
        if (footer) footer.style.display = 'none';
        this.loadMyStickers(searchInput ? searchInput.value.trim() : '');
      } else {
        if (searchWrap) searchWrap.style.display = 'flex';
        if (searchInput) searchInput.placeholder = 'Search stickers with KLIPY...';
        if (catBar) catBar.style.display = 'flex';
        if (footer) footer.style.display = 'flex';
        this.loadStickers(true);
      }
    },

    insertEmoji(emoji) {
      const input = this.currentTargetInput;
      if (!input) return;

      if (input.isContentEditable || input.getAttribute('contenteditable') === 'true') {
        input.focus();
        document.execCommand('insertText', false, emoji);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        return;
      }

      const start = input.selectionStart || 0;
      const end = input.selectionEnd || 0;
      const val = input.value || '';

      input.value = val.substring(0, start) + emoji + val.substring(end);
      const newPos = start + emoji.length;
      input.selectionStart = newPos;
      input.selectionEnd = newPos;

      // Trigger input & change events for validation & autosize
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));

      // Keep focus on input so user can seamlessly keep typing or sending
      input.focus();
    },

    attachMedia({ type, url, previewUrl, meta = {} }) {
      // In chat conversation: tap to send immediately directly to chat stream!
      const isChatContext = Boolean(
        document.getElementById('messagesContainer') ||
        (this.currentTargetInput && (this.currentTargetInput.id === 'messageInput' || this.currentTargetInput.closest('.chat-input-container')))
      );

      if (isChatContext && (type === 'sticker' || type === 'gif')) {
        this.close();
        if (typeof window.chatSendDirectMedia === 'function') {
          window.chatSendDirectMedia({ type, url, previewUrl, meta });
        } else {
          window.dispatchEvent(new CustomEvent('chatSendDirectMedia', {
            detail: { type, url, previewUrl, meta }
          }));
        }
        return;
      }

      const form = this.currentForm || (this.currentTargetInput ? (this.currentTargetInput.closest('form') || this.currentTargetInput.closest('.comment-composer')) : null);
      if (!form) {
        console.warn('[MediaPicker] No active comment form found for attachment');
        this.close();
        return;
      }

      // Check or create attachment hidden inputs on the form
      this.setHiddenInput(form, 'attachment_type', type);
      this.setHiddenInput(form, 'attachment_url', url);
      this.setHiddenInput(form, 'attachment_meta', JSON.stringify(meta));

      // Remove existing attachment image file input value if any
      const fileInput = form.querySelector('input[type="file"].comment-photo-input');
      if (fileInput) fileInput.value = '';

      // Render preview chip above comment input
      this.renderAttachmentPreviewChip(form, {
        type,
        thumbUrl: previewUrl || url,
        title: meta.title || type.toUpperCase()
      });

      this.close();

      // Enable send button if disabled
      const sendBtn = form.querySelector('button[type="submit"], [data-action="send-reply"]');
      if (sendBtn) sendBtn.disabled = false;

      // Focus input so user can type text or hit Enter
      const textInput = form.querySelector('input[type="text"], textarea');
      if (textInput) textInput.focus();
    },

    attachLocalImage(file, form) {
      if (!file || !form) return;
      form._pastedAttachmentFile = file;

      const fileInput = form.querySelector('input[type="file"].comment-photo-input') || form.querySelector('input[type="file"][name="attachment_image"]');
      if (fileInput) {
        try {
          const dt = new DataTransfer();
          dt.items.add(file);
          fileInput.files = dt.files;
        } catch (_) {}
      }

      const reader = new FileReader();
      reader.onload = (e) => {
        const dataUrl = e.target.result;

        this.setHiddenInput(form, 'attachment_type', 'image');
        this.setHiddenInput(form, 'attachment_url', '');
        this.setHiddenInput(form, 'attachment_meta', JSON.stringify({
          name: file.name || 'screenshot.png',
          size: file.size,
          type: file.type || 'image/png'
        }));

        const isScreenshot = !file.name || file.name.includes('screenshot') || file.name.includes('clipboard') || file.name === 'image.png';
        this.renderAttachmentPreviewChip(form, {
          type: 'image',
          thumbUrl: dataUrl,
          title: isScreenshot ? 'Screenshot' : 'Photo Sticker'
        });

        const sendBtn = form.querySelector('button[type="submit"], [data-action="send-reply"], .comment-send-btn, .main-composer-send');
        if (sendBtn) sendBtn.disabled = false;
      };
      reader.readAsDataURL(file);
    },

    renderAttachmentPreviewChip(form, { type, thumbUrl, title }) {
      let previewContainer = form.querySelector('.comment-attachment-preview-container');
      if (!previewContainer) {
        previewContainer = document.createElement('div');
        previewContainer.className = 'comment-attachment-preview-container';
        const inputWrap = form.querySelector('.flex-grow-1, .main-composer-input-wrapper, .composer-input, input, textarea');
        if (inputWrap) {
          inputWrap.parentElement.insertBefore(previewContainer, inputWrap);
        } else {
          form.insertBefore(previewContainer, form.firstChild);
        }
      }

      const isSticker = type === 'sticker';
      const badgeText = type === 'gif' ? 'GIF' : (type === 'sticker' ? 'STICKER' : 'PHOTO');

      previewContainer.innerHTML = `
        <div class="comment-attachment-preview-chip">
          <img src="${thumbUrl}" class="comment-attachment-preview-thumb ${isSticker ? 'is-sticker' : ''}" alt="Attachment">
          <div class="comment-attachment-preview-info">
            <span class="comment-attachment-preview-badge">${badgeText}</span>
            <span class="comment-attachment-preview-text">${title || 'Attachment'}</span>
          </div>
          <button type="button" class="comment-attachment-remove-btn" title="Remove attachment" aria-label="Remove attachment">
            <i aria-hidden="true" data-lucide="x" class="vasto-icon"></i>
          </button>
        </div>
      `;
    },

    clearAttachment(form) {
      if (!form) return;
      delete form._pastedAttachmentFile;
      this.setHiddenInput(form, 'attachment_type', 'none');
      this.setHiddenInput(form, 'attachment_url', '');
      this.setHiddenInput(form, 'attachment_meta', '{}');

      const fileInput = form.querySelector('input[type="file"].comment-photo-input') || form.querySelector('input[type="file"][name="attachment_image"]');
      if (fileInput) fileInput.value = '';

      const previewContainer = form.querySelector('.comment-attachment-preview-container');
      if (previewContainer) {
        previewContainer.innerHTML = '';
      }

      // Check if text input is empty, disable send button if empty
      const textInput = form.querySelector('.composer-input-editable, input[type="text"], textarea');
      const val = textInput ? (textInput.value || textInput.innerText || '').trim() : '';
      const sendBtn = form.querySelector('button[type="submit"], [data-action="send-reply"], .comment-send-btn, .main-composer-send');
      if (sendBtn && !val) {
        sendBtn.disabled = true;
      }
    },

    initPasteSupport() {
      this.bindAllEditableComposers();

      // Listen for HTMX swaps to bind dynamic composers
      document.addEventListener('htmx:afterSwap', (e) => {
        this.bindAllEditableComposers(e.target || document);
      });
      document.addEventListener('focusin', (e) => {
        if (e.target && e.target.classList && e.target.classList.contains('composer-input-editable')) {
          this.bindEditableComposer(e.target);
        }
      });
    },

    bindAllEditableComposers(root = document) {
      const editables = root.querySelectorAll('.composer-input-editable[contenteditable="true"]');
      editables.forEach(el => this.bindEditableComposer(el));
    },

    bindEditableComposer(el) {
      if (!el || el.dataset.editableBound === 'true') return;
      el.dataset.editableBound = 'true';

      const form = el.closest('form') || el.closest('.comment-composer');
      let hiddenInput = form?.querySelector('input[type="hidden"].composer-hidden-content, input[type="hidden"][name="content"]');

      if (form && !hiddenInput) {
        hiddenInput = document.createElement('input');
        hiddenInput.type = 'hidden';
        hiddenInput.name = 'content';
        hiddenInput.className = 'composer-hidden-content';
        form.appendChild(hiddenInput);
      }

      // Provide .value getter/setter for maximum compatibility with existing JS
      try {
        Object.defineProperty(el, 'value', {
          get() {
            return (this.innerText || this.textContent || '').trim();
          },
          set(v) {
            this.textContent = v || '';
            if (hiddenInput) hiddenInput.value = (v || '').trim();
          },
          configurable: true
        });
      } catch (_) {}

      // Provide .placeholder getter/setter
      try {
        Object.defineProperty(el, 'placeholder', {
          get() {
            return this.getAttribute('data-placeholder') || '';
          },
          set(v) {
            this.setAttribute('data-placeholder', v || '');
          },
          configurable: true
        });
      } catch (_) {}

      // Keep hidden input in sync as user types
      el.addEventListener('input', () => {
        const val = (el.innerText || el.textContent || '').trim();
        if (hiddenInput) hiddenInput.value = val;

        const sendBtn = form?.querySelector('button[type="submit"], [data-action="send-reply"], .comment-send-btn, .main-composer-send');
        const hasAttachment = form?.querySelector('.comment-attachment-preview-chip');
        if (sendBtn) {
          sendBtn.disabled = !val && !hasAttachment;
        }
      });

      // Submit on Enter (unless Shift+Enter)
      el.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          if (form) {
            if (typeof form.requestSubmit === 'function') {
              form.requestSubmit();
            } else {
              const submitBtn = form.querySelector('button[type="submit"], [data-action="send-reply"], .comment-send-btn, .main-composer-send');
              if (submitBtn) submitBtn.click();
              else form.dispatchEvent(new Event('submit', { cancelable: true }));
            }
          }
        }
      });

      // Clear when form resets
      if (form) {
        form.addEventListener('reset', () => {
          setTimeout(() => {
            el.textContent = '';
            if (hiddenInput) hiddenInput.value = '';
          }, 0);
        });
      }
    },

    setHiddenInput(form, name, value) {
      let input = form.querySelector(`input[name="${name}"]`);
      if (!input) {
        input = document.createElement('input');
        input.type = 'hidden';
        input.name = name;
        form.appendChild(input);
      }
      input.value = value;
    },

    // =========================================================================
    // KLIPY GIFs Loading & Rendering
    // =========================================================================
    async loadGifs(reset = false) {
      if (this.isLoadingGifs) return;
      if (!reset && !this.hasMoreGifs) return;

      this.isLoadingGifs = true;
      const currentReqId = ++this.gifRequestId;

      const grid = document.getElementById('mediaPickerGifGrid');
      if (!grid) {
        this.isLoadingGifs = false;
        return;
      }

      if (reset) {
        this.gifPage = 1;
        this.hasMoreGifs = true;
        const msg = this.gifQuery ? `Searching "${this.escapeHtml(this.gifQuery)}"...` : 'Loading GIFs...';
        grid.innerHTML = `<div class="media-picker-loading"><div class="spinner-border spinner-border-sm text-primary"></div><span>${msg}</span></div>`;
        grid.scrollTop = 0;
      } else {
        const existingLoader = grid.querySelector('.media-picker-pagination-loader');
        if (!existingLoader) {
          const loader = document.createElement('div');
          loader.className = 'media-picker-pagination-loader';
          loader.innerHTML = '<div class="spinner-border spinner-border-sm text-primary"></div><span>Loading more GIFs...</span>';
          grid.appendChild(loader);
        }
      }

      try {
        const q = encodeURIComponent(this.gifQuery || '');
        const url = `/api/media/klipy/?type=gifs&q=${q}&page=${this.gifPage}&per_page=24`;
        const res = await fetch(url);
        const json = await res.json();

        // Stale query response protection
        if (currentReqId !== this.gifRequestId) {
          return;
        }

        this._gifsLoaded = true;
        const items = json?.data?.data || [];

        // Remove loaders
        const bottomLoader = grid.querySelector('.media-picker-pagination-loader');
        if (bottomLoader) bottomLoader.remove();
        const mainLoader = grid.querySelector('.media-picker-loading');
        if (mainLoader) mainLoader.remove();

        if (reset) {
          grid.innerHTML = '';
        }

        if (json?.error) {
          console.warn('[MediaPicker] Klipy GIFs error:', json.error);
        }

        if (!items || items.length === 0) {
          if (reset && grid.children.length === 0) {
            const isError = Boolean(json?.error);
            const emptyMsg = this.gifQuery
              ? `No GIFs found for "${this.escapeHtml(this.gifQuery)}"`
              : (isError ? 'Unable to load GIFs at the moment' : 'No GIFs available at the moment');
            grid.innerHTML = `
              <div class="media-picker-empty">
                <i aria-hidden="true" data-lucide="video" class="vasto-icon text-muted"></i>
                <span class="fw-semibold">${emptyMsg}</span>
                <button type="button" class="media-picker-empty-action" data-action="clear-gif-search"><i aria-hidden="true" data-lucide="refresh-cw" class="vasto-icon me-1"></i>${isError ? 'Retry' : 'Browse Trending GIFs'}</button>
              </div>
            `;
          }
          this.hasMoreGifs = false;
          return;
        }

        if (items.length < 24) {
          this.hasMoreGifs = false;
        }

        items.forEach(item => {
          const files = item.file || {};
          // Direct media loading per KLIPY requirements
          const gifUrl = files.md?.gif?.url || files.hd?.gif?.url || files.sm?.gif?.url || '';
          const previewUrl = files.sm?.webp?.url || files.sm?.gif?.url || files.xs?.gif?.url || gifUrl;
          if (!gifUrl) return;

          const card = document.createElement('div');
          card.className = 'media-picker-item';
          card.setAttribute('title', item.title || 'GIF');
          card.innerHTML = `<img src="${previewUrl}" alt="${this.escapeHtml(item.title || 'GIF')}" loading="lazy">`;

          card.addEventListener('click', () => {
            this.attachMedia({
              type: 'gif',
              url: gifUrl,
              previewUrl: previewUrl,
              meta: {
                id: item.id,
                slug: item.slug,
                title: item.title,
                width: files.md?.gif?.width || 320,
                height: files.md?.gif?.height || 240
              }
            });
          });

          grid.appendChild(card);
        });

      } catch (err) {
        if (currentReqId !== this.gifRequestId) return;
        console.error('[MediaPicker] Error loading GIFs:', err);
        const bottomLoader = grid.querySelector('.media-picker-pagination-loader');
        if (bottomLoader) bottomLoader.remove();
        if (reset) {
          grid.innerHTML = `
            <div class="media-picker-empty text-danger">
              <i aria-hidden="true" data-lucide="circle-alert" class="vasto-icon"></i>
              <span>Failed to load GIFs. Check connection.</span>
              <button type="button" class="media-picker-empty-action" data-action="retry-gif-search">Try Again</button>
            </div>
          `;
        }
      } finally {
        if (currentReqId === this.gifRequestId) {
          this.isLoadingGifs = false;
        }
      }
    },

    // =========================================================================
    // Stickers Loading & Rendering
    // =========================================================================
    async loadStickers(reset = false) {
      if (this.activeStickerSubtab === 'builtin') {
        const searchInput = document.getElementById('mediaPickerStickerSearch');
        this.renderBuiltinStickers(searchInput ? searchInput.value.trim() : '');
        return;
      } else if (this.activeStickerSubtab === 'mine') {
        const searchInput = document.getElementById('mediaPickerStickerSearch');
        this.loadMyStickers(searchInput ? searchInput.value.trim() : '');
        return;
      }

      // KLIPY Stickers
      if (this.isLoadingStickers) return;
      if (!reset && !this.hasMoreStickers) return;

      this.isLoadingStickers = true;
      const currentReqId = ++this.stickerRequestId;

      const grid = document.getElementById('mediaPickerStickerGrid');
      if (!grid) {
        this.isLoadingStickers = false;
        return;
      }

      if (reset) {
        this.stickerPage = 1;
        this.hasMoreStickers = true;
        const msg = this.stickerQuery ? `Searching "${this.escapeHtml(this.stickerQuery)}"...` : 'Loading stickers...';
        grid.innerHTML = `<div class="media-picker-loading"><div class="spinner-border spinner-border-sm text-primary"></div><span>${msg}</span></div>`;
        grid.scrollTop = 0;
      } else {
        const existingLoader = grid.querySelector('.media-picker-pagination-loader');
        if (!existingLoader) {
          const loader = document.createElement('div');
          loader.className = 'media-picker-pagination-loader';
          loader.innerHTML = '<div class="spinner-border spinner-border-sm text-primary"></div><span>Loading more stickers...</span>';
          grid.appendChild(loader);
        }
      }

      try {
        const q = encodeURIComponent(this.stickerQuery || '');
        const url = `/api/media/klipy/?type=stickers&q=${q}&page=${this.stickerPage}&per_page=24`;
        const res = await fetch(url);
        const json = await res.json();

        // Stale query response protection
        if (currentReqId !== this.stickerRequestId) return;

        this._stickersLoaded = true;
        const items = json?.data?.data || [];

        const bottomLoader = grid.querySelector('.media-picker-pagination-loader');
        if (bottomLoader) bottomLoader.remove();
        const mainLoader = grid.querySelector('.media-picker-loading');
        if (mainLoader) mainLoader.remove();

        if (reset) {
          grid.innerHTML = '';
          // Show matching built-in SVG stickers first if search term matches
          if (this.stickerQuery) {
            const queryNorm = this.stickerQuery.toLowerCase().trim();
            const matchingBuiltin = BUILTIN_STICKERS.filter(st => st.name.toLowerCase().includes(queryNorm));
            matchingBuiltin.forEach(st => {
              const card = document.createElement('div');
              card.className = 'media-picker-item';
              card.setAttribute('title', `${st.name} (Built-in)`);
              const stUrl = `/static/images/stickers/${st.file}`;
              card.innerHTML = `<img src="${stUrl}" alt="${this.escapeHtml(st.name)}" loading="lazy">`;
              card.addEventListener('click', () => {
                this.attachMedia({
                  type: 'sticker',
                  url: stUrl,
                  previewUrl: stUrl,
                  meta: { title: st.name, source: 'builtin' }
                });
              });
              grid.appendChild(card);
            });
          }
        }

        if (json?.error) {
          console.warn('[MediaPicker] Klipy Stickers error:', json.error);
        }

        if (!items || items.length === 0) {
          if (reset && grid.children.length === 0) {
            const isError = Boolean(json?.error);
            const emptyMsg = this.stickerQuery
              ? `No stickers found for "${this.escapeHtml(this.stickerQuery)}"`
              : (isError ? 'Unable to load stickers at the moment' : 'No stickers available');
            grid.innerHTML = `
              <div class="media-picker-empty">
                <i aria-hidden="true" data-lucide="sticky-note" class="vasto-icon text-muted"></i>
                <span class="fw-semibold">${emptyMsg}</span>
                <div class="d-flex gap-2 justify-content-center mt-2 flex-wrap">
                  ${isError ? '<button type="button" class="media-picker-empty-action" data-action="switch-to-builtin-stickers"><i aria-hidden="true" data-lucide="sparkles" class="vasto-icon me-1"></i>Open Built-in Stickers</button>' : ''}
                  <button type="button" class="media-picker-empty-action" data-action="clear-sticker-search"><i aria-hidden="true" data-lucide="refresh-cw" class="vasto-icon me-1"></i>${isError ? 'Retry' : 'Browse Trending Stickers'}</button>
                </div>
              </div>
            `;
          }
          this.hasMoreStickers = false;
          return;
        }

        if (items.length < 24) {
          this.hasMoreStickers = false;
        }

        items.forEach(item => {
          const files = item.file || {};
          const stickerUrl = files.md?.webp?.url || files.md?.png?.url || files.hd?.webp?.url || files.hd?.gif?.url || '';
          const previewUrl = files.sm?.webp?.url || files.sm?.png?.url || stickerUrl;
          if (!stickerUrl) return;

          const card = document.createElement('div');
          card.className = 'media-picker-item';
          card.setAttribute('title', item.title || 'Sticker');
          card.innerHTML = `<img src="${previewUrl}" alt="${this.escapeHtml(item.title || 'Sticker')}" loading="lazy">`;

          card.addEventListener('click', () => {
            this.attachMedia({
              type: 'sticker',
              url: stickerUrl,
              previewUrl: previewUrl,
              meta: {
                id: item.id,
                slug: item.slug,
                title: item.title,
                source: 'klipy'
              }
            });
          });

          grid.appendChild(card);
        });

      } catch (err) {
        if (currentReqId !== this.stickerRequestId) return;
        console.error('[MediaPicker] Error loading stickers:', err);
        const bottomLoader = grid.querySelector('.media-picker-pagination-loader');
        if (bottomLoader) bottomLoader.remove();
        if (reset && grid.children.length === 0) {
          grid.innerHTML = `
            <div class="media-picker-empty text-danger">
              <i aria-hidden="true" data-lucide="circle-alert" class="vasto-icon"></i>
              <span>Failed to load stickers.</span>
              <button type="button" class="media-picker-empty-action" data-action="retry-sticker-search">Try Again</button>
            </div>
          `;
        }
      } finally {
        if (currentReqId === this.stickerRequestId) {
          this.isLoadingStickers = false;
        }
      }
    },

    renderBuiltinStickers(filterQuery = '') {
      const grid = document.getElementById('mediaPickerStickerGrid');
      if (!grid) return;

      const q = (filterQuery || this.stickerQuery || '').toLowerCase().trim();
      const filtered = q
        ? BUILTIN_STICKERS.filter(st => st.name.toLowerCase().includes(q))
        : BUILTIN_STICKERS;

      grid.innerHTML = '';

      if (filtered.length === 0) {
        grid.innerHTML = `
          <div class="media-picker-empty">
            <i aria-hidden="true" data-lucide="sticky-note" class="vasto-icon text-muted"></i>
            <span class="fw-semibold">No built-in stickers found for "${this.escapeHtml(q)}"</span>
            <button type="button" class="media-picker-empty-action" data-action="switch-to-klipy-stickers"><i aria-hidden="true" data-lucide="search" class="vasto-icon me-1"></i>Search in KLIPY Stickers</button>
          </div>
        `;
        return;
      }

      filtered.forEach(st => {
        const card = document.createElement('div');
        card.className = 'media-picker-item';
        card.setAttribute('title', st.name);
        const stUrl = `/static/images/stickers/${st.file}`;
        card.innerHTML = `<img src="${stUrl}" alt="${this.escapeHtml(st.name)}" loading="lazy">`;

        card.addEventListener('click', () => {
          this.attachMedia({
            type: 'sticker',
            url: stUrl,
            previewUrl: stUrl,
            meta: {
              title: st.name,
              source: 'builtin'
            }
          });
        });

        grid.appendChild(card);
      });
    },

    async loadMyStickers(filterQuery = '') {
      const grid = document.getElementById('mediaPickerStickerGrid');
      if (!grid) return;

      const q = (filterQuery || this.stickerQuery || '').toLowerCase().trim();

      if (!this._myStickersCache) {
        grid.innerHTML = '<div class="media-picker-loading"><div class="spinner-border spinner-border-sm text-primary"></div><span>Loading your stickers...</span></div>';

        try {
          const res = await fetch('/api/stickers/mine/');
          if (!res.ok) {
            if (res.status === 401 || res.status === 403) {
              grid.innerHTML = '<div class="media-picker-empty"><i aria-hidden="true" data-lucide="lock" class="vasto-icon"></i><span>Please log in to save and use custom stickers.</span></div>';
              return;
            }
            throw new Error('Failed to load user stickers');
          }

          this._myStickersCache = await res.json();
        } catch (err) {
          console.error('[MediaPicker] Error loading user stickers:', err);
          grid.innerHTML = '<div class="media-picker-empty text-danger"><i aria-hidden="true" data-lucide="circle-alert" class="vasto-icon"></i><span>Failed to load custom stickers.</span></div>';
          return;
        }
      }

      const stickers = (this._myStickersCache || []).filter(st => {
        if (!q) return true;
        const name = (st.name || '').toLowerCase();
        return name.includes(q);
      });

      grid.innerHTML = '';

      // First item: "+ Add Sticker" button
      const uploadCard = document.createElement('div');
      uploadCard.className = 'media-picker-upload-card';
      uploadCard.innerHTML = `<i aria-hidden="true" data-lucide="circle-plus" class="vasto-icon"></i><span>Upload</span>`;
      uploadCard.addEventListener('click', () => {
        const fileInput = document.getElementById('mediaPickerCustomStickerInput');
        if (fileInput) fileInput.click();
      });
      grid.appendChild(uploadCard);

      if (stickers.length === 0 && q) {
        const emptyDiv = document.createElement('div');
        emptyDiv.className = 'media-picker-empty';
        emptyDiv.innerHTML = `<span class="text-muted">No custom stickers matching "${this.escapeHtml(q)}"</span>`;
        grid.appendChild(emptyDiv);
        return;
      }

      stickers.forEach(st => {
        const card = document.createElement('div');
        card.className = 'media-picker-item';
        card.setAttribute('title', st.name || 'My Sticker');
        card.innerHTML = `<img src="${st.image}" alt="${this.escapeHtml(st.name || 'Sticker')}" loading="lazy">`;

        card.addEventListener('click', () => {
          this.attachMedia({
            type: 'sticker',
            url: st.image,
            previewUrl: st.image,
            meta: {
              id: st.id,
              title: st.name || 'Custom Sticker',
              source: 'user'
            }
          });
        });

        grid.appendChild(card);
      });
    },

    async uploadCustomSticker(file) {
      const formData = new FormData();
      formData.append('image', file);
      formData.append('name', file.name.replace(/\.[^/.]+$/, ""));

      const grid = document.getElementById('mediaPickerStickerGrid');
      if (grid) {
        grid.insertAdjacentHTML('afterbegin', '<div class="media-picker-loading uploading-pill"><div class="spinner-border spinner-border-sm text-primary"></div><span>Uploading sticker...</span></div>');
      }

      try {
        const csrfToken = this.getCsrfToken();
        const res = await fetch('/api/stickers/mine/', {
          method: 'POST',
          headers: {
            'X-CSRFToken': csrfToken
          },
          body: formData
        });

        if (!res.ok) throw new Error('Sticker upload failed');
        this.loadMyStickers();
      } catch (err) {
        console.error('[MediaPicker] Sticker upload failed:', err);
        alert('Failed to upload sticker. Please ensure image is under 5MB.');
        this.loadMyStickers();
      }
    },

    getCsrfToken() {
      const meta = document.querySelector('meta[name="csrf-token"]');
      if (meta && meta.getAttribute('content')) {
        const val = meta.getAttribute('content').trim();
        if (val && !val.includes('{{')) return val;
      }
      const domInput = (this.activeForm && this.activeForm.querySelector('[name=csrfmiddlewaretoken]')) ||
                       document.querySelector('[name=csrfmiddlewaretoken]');
      if (domInput && domInput.value) return domInput.value;
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
  };

  // Expose globally
  window.MediaPicker = MediaPicker;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => MediaPicker.init());
  } else {
    MediaPicker.init();
  }

})(window, document);
