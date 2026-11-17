/**
 * Chat Theme Manager
 * Handles per-conversation theme customization
 */
class ChatThemeManager {
    constructor() {
        this.currentConversationId = null;
        this.currentTheme = null;
        this.currentThemeMode = 'light';
        this.uploadedImages = {
            light: null,
            dark: null
        };
        this.isPreviewMode = false;
        this.imagePosition = {
            x: 50,
            y: 50
        };
        this.isDragging = false;
        this.init();
    }

    init() {
        this.setupEventListeners();
        this.detectThemeMode();
        this.loadThemeForCurrentConversation();
    }

    setupEventListeners() {
        // Theme selector modal controls
        document.getElementById('closeThemeModal')?.addEventListener('click', () => this.hideThemeSelector());
        document.getElementById('themeCancelBtn')?.addEventListener('click', () => this.hideThemeSelector());
        document.getElementById('themeSaveBtn')?.addEventListener('click', () => this.saveTheme());
        document.getElementById('themeResetBtn')?.addEventListener('click', () => this.resetTheme());

        // Theme type tabs
        document.querySelectorAll('.theme-tab').forEach(tab => {
            tab.addEventListener('click', (e) => this.switchThemeTab(e.target.dataset.themeType));
        });

        // Color inputs
        this.setupColorInputs();

        // Gradient angle slider
        const angleSlider = document.getElementById('gradientAngle');
        if (angleSlider) {
            angleSlider.addEventListener('input', (e) => {
                document.getElementById('angleValue').textContent = e.target.value;
                this.updateGradientPreview();
            });
        }

        // Overlay opacity slider
        const opacitySlider = document.getElementById('overlayOpacity');
        if (opacitySlider) {
            opacitySlider.addEventListener('input', (e) => {
                document.getElementById('opacityValue').textContent = e.target.value;
                this.updateOverlayPreview();
            });
        }

        // Image uploads
        this.setupImageUploads();

        // Theme mode change detection
        const observer = new MutationObserver(() => {
            this.detectThemeMode();
            this.applyTheme();
        });
        observer.observe(document.documentElement, {
            attributes: true,
            attributeFilter: ['data-theme']
        });

        // Theme button in chat header (add this to chat header)
        this.addThemeButtonToHeader();

        // Setup drag-to-position for image backgrounds
        this.setupDragToPosition();
    }

    setupColorInputs() {
        // Solid color inputs
        this.linkColorInputs('lightColor', 'lightColorText');
        this.linkColorInputs('darkColor', 'darkColorText');
        
        // Gradient color inputs
        this.linkColorInputs('lightGradientStart', 'lightGradientStartText');
        this.linkColorInputs('lightGradientEnd', 'lightGradientEndText');
        this.linkColorInputs('darkGradientStart', 'darkGradientStartText');
        this.linkColorInputs('darkGradientEnd', 'darkGradientEndText');
        
        // Overlay color inputs
        this.linkColorInputs('lightOverlayColor', 'lightOverlayColorText');
        this.linkColorInputs('darkOverlayColor', 'darkOverlayColorText');
    }

    linkColorInputs(colorInputId, textInputId) {
        const colorInput = document.getElementById(colorInputId);
        const textInput = document.getElementById(textInputId);
        
        if (colorInput && textInput) {
            colorInput.addEventListener('input', (e) => {
                textInput.value = e.target.value;
                this.updatePreview();
            });
            
            textInput.addEventListener('input', (e) => {
                if (/^#[0-9A-Fa-f]{6}$/.test(e.target.value)) {
                    colorInput.value = e.target.value;
                    this.updatePreview();
                }
            });
        }
    }

    setupImageUploads() {
        const lightUpload = document.getElementById('lightImageUpload');
        const darkUpload = document.getElementById('darkImageUpload');
        
        if (lightUpload) {
            lightUpload.addEventListener('click', () => {
                document.getElementById('lightImageInput').click();
            });
            
            document.getElementById('lightImageInput').addEventListener('change', (e) => {
                this.handleImageUpload(e, 'light');
            });
        }
        
        if (darkUpload) {
            darkUpload.addEventListener('click', () => {
                document.getElementById('darkImageInput').click();
            });
            
            document.getElementById('darkImageInput').addEventListener('change', (e) => {
                this.handleImageUpload(e, 'dark');
            });
        }
    }

    setupDragToPosition() {
        const messagesArea = document.querySelector('.messages-area');
        if (!messagesArea) return;

        messagesArea.addEventListener('mousedown', (e) => {
            if (this.isPreviewMode && e.target === messagesArea) {
                this.isDragging = true;
                this.updateImagePosition(e);
                e.preventDefault();
            }
        });

        messagesArea.addEventListener('mousemove', (e) => {
            if (this.isDragging) {
                this.updateImagePosition(e);
                e.preventDefault();
            }
        });

        messagesArea.addEventListener('mouseup', () => {
            this.isDragging = false;
        });

        messagesArea.addEventListener('mouseleave', () => {
            this.isDragging = false;
        });

        // Touch events for mobile
        messagesArea.addEventListener('touchstart', (e) => {
            if (this.isPreviewMode && e.target === messagesArea) {
                this.isDragging = true;
                const touch = e.touches[0];
                this.updateImagePosition(touch);
                e.preventDefault();
            }
        });

        messagesArea.addEventListener('touchmove', (e) => {
            if (this.isDragging) {
                const touch = e.touches[0];
                this.updateImagePosition(touch);
                e.preventDefault();
            }
        });

        messagesArea.addEventListener('touchend', () => {
            this.isDragging = false;
        });
    }

    updateImagePosition(e) {
        const messagesArea = document.querySelector('.messages-area');
        if (!messagesArea) return;

        const rect = messagesArea.getBoundingClientRect();
        const x = ((e.clientX - rect.left) / rect.width) * 100;
        const y = ((e.clientY - rect.top) / rect.height) * 100;

        this.imagePosition.x = Math.max(0, Math.min(100, x));
        this.imagePosition.y = Math.max(0, Math.min(100, y));

        // Update background position
        messagesArea.style.backgroundPosition = `${this.imagePosition.x}% ${this.imagePosition.y}%`;

        // Show position indicator
        this.showPositionIndicator();
    }

    showPositionIndicator() {
        const messagesArea = document.querySelector('.messages-area');
        if (!messagesArea) return;

        // Remove existing indicator
        const existingIndicator = messagesArea.querySelector('.position-indicator');
        if (existingIndicator) existingIndicator.remove();

        // Create position indicator
        const indicator = document.createElement('div');
        indicator.className = 'position-indicator';
        indicator.style.cssText = `
            position: absolute;
            left: ${this.imagePosition.x}%;
            top: ${this.imagePosition.y}%;
            transform: translate(-50%, -50%);
            width: 20px;
            height: 20px;
            border: 2px solid white;
            border-radius: 50%;
            background: rgba(0, 0, 0, 0.5);
            pointer-events: none;
            z-index: 10;
            animation: pulse 1s infinite;
        `;

        messagesArea.appendChild(indicator);

        // Auto-hide after 2 seconds
        setTimeout(() => {
            if (indicator.parentNode) {
                indicator.remove();
            }
        }, 2000);
    }

    handleImageUpload(event, mode) {
        const file = event.target.files[0];
        if (file && file.type.startsWith('image/')) {
            const reader = new FileReader();
            reader.onload = (e) => {
                const previewId = mode === 'light' ? 'lightImagePreview' : 'darkImagePreview';
                const preview = document.getElementById(previewId);
                if (preview) {
                    preview.style.backgroundImage = `url(${e.target.result})`;
                    preview.parentElement.querySelector('.upload-placeholder').style.display = 'none';
                }
                // Store the data URL for later use
                this.uploadedImages[mode] = e.target.result;
                this.updatePreview();
            };
            reader.readAsDataURL(file);
        }
    }

    addThemeButtonToHeader() {
        const chatHeader = document.querySelector('.chat-header');
        if (chatHeader && !chatHeader.querySelector('.theme-btn')) {
            const themeBtn = document.createElement('button');
            themeBtn.className = 'theme-btn';
            themeBtn.innerHTML = '<i aria-hidden="true" data-lucide="palette" class="vasto-icon"></i>';
            themeBtn.title = 'Chat Theme';
            themeBtn.addEventListener('click', () => this.showThemeSelector());
            
            // Insert before the options button if it exists
            const optionsBtn = chatHeader.querySelector('.options-btn');
            if (optionsBtn) {
                chatHeader.insertBefore(themeBtn, optionsBtn);
            } else {
                chatHeader.appendChild(themeBtn);
            }
        }
    }

    detectThemeMode() {
        const htmlElement = document.documentElement;
        this.currentThemeMode = htmlElement.getAttribute('data-theme') || 'light';
    }

    async loadThemeForCurrentConversation() {
        // Get conversation ID from URL or data attribute
        this.currentConversationId = this.getCurrentConversationId();
        
        console.log('=== LOAD THEME FOR CONVERSATION ===');
        console.log('currentConversationId:', this.currentConversationId);
        
        if (!this.currentConversationId) {
            console.log('No conversation ID found');
            return;
        }

        try {
            const response = await fetch(`/messaging/v1/themes/by_conversation/?conversation_id=${this.currentConversationId}`, {
                headers: {
                    'X-CSRFToken': this.getCSRFToken()
                }
            });
            
            console.log('Theme response status:', response.status);
            console.log('Theme response ok:', response.ok);
            
            if (response.ok) {
                this.currentTheme = await response.json();
                console.log('Theme loaded successfully:', this.currentTheme);
                this.applyTheme();
                this.populateThemeForm();
            } else {
                console.log('Theme response not ok, using default');
                const error = await response.json();
                console.log('Theme load error:', error);
                // Apply default theme if none exists
                this.applyDefaultTheme();
            }
        } catch (error) {
            console.error('Failed to load theme:', error);
            // Apply default theme on error
            this.applyDefaultTheme();
        }
    }

    getCurrentConversationId() {
        // Try to get from URL path
        const pathMatch = window.location.pathname.match(/\/conversation\/(\d+)\//);
        if (pathMatch) return parseInt(pathMatch[1]);
        
        // Try to get from data attribute
        const chatContainer = document.querySelector('.chat-container');
        if (chatContainer) {
            return parseInt(chatContainer.dataset.conversationId);
        }
        
        return null;
    }

    applyTheme() {
        console.log('=== APPLY THEME CALLED ===');
        console.log('currentTheme:', this.currentTheme);
        console.log('currentThemeMode:', this.currentThemeMode);

        if (!this.currentTheme) {
            console.log('No current theme to apply');
            return;
        }

        const cssVars = this.currentThemeMode === 'light' ?
            this.currentTheme.css_variables_light :
            this.currentTheme.css_variables_dark;

        const overlayVars = this.currentThemeMode === 'light' ?
            this.currentTheme.overlay_light :
            this.currentTheme.overlay_dark;

        console.log('cssVars:', cssVars);
        console.log('overlayVars:', overlayVars);

        if (cssVars) {
            Object.entries(cssVars).forEach(([key, value]) => {
                document.documentElement.style.setProperty(key, value);
            });
        }

        if (overlayVars) {
            Object.entries(overlayVars).forEach(([key, value]) => {
                document.documentElement.style.setProperty(key, value);
            });
        }

        // Apply theme to chat messages area
        this.applyThemeToChatArea();
    }

    applyThemeToChatArea() {
        const messagesArea = document.querySelector('.messages-area');
        if (!messagesArea) return;

        const chatBg = getComputedStyle(document.documentElement).getPropertyValue('--chat-bg');
        const chatBgType = getComputedStyle(document.documentElement).getPropertyValue('--chat-bg-type');
        const chatBgFit = getComputedStyle(document.documentElement).getPropertyValue('--chat-bg-fit');
        const overlayColor = getComputedStyle(document.documentElement).getPropertyValue('--chat-overlay-color');
        const overlayOpacity = getComputedStyle(document.documentElement).getPropertyValue('--chat-overlay-opacity');

        console.log('=== THEME MANAGER APPLYING TO CHAT AREA ===');
        console.log('chatBg:', chatBg);
        console.log('chatBgType:', chatBgType);
        console.log('chatBgFit:', chatBgFit);

        // Apply background
        if (chatBg && chatBg !== 'none') {
            if (chatBgType === 'image') {
                console.log('Applying image background');
                messagesArea.style.backgroundImage = chatBg;
                messagesArea.style.backgroundSize = chatBgFit || 'cover';
                messagesArea.style.backgroundPosition = `${this.imagePosition.x}% ${this.imagePosition.y}%`;
                messagesArea.style.backgroundRepeat = chatBgFit === 'repeat' ? 'repeat' : 'no-repeat';
            } else {
                console.log('Applying solid/gradient background');
                messagesArea.style.background = chatBg;
            }
        } else {
            console.log('No background to apply (chatBg is none or empty)');
        }

        // Apply overlay for readability
        if (overlayColor && overlayOpacity) {
            const existingOverlay = messagesArea.querySelector('.chat-overlay');
            if (existingOverlay) {
                existingOverlay.style.backgroundColor = overlayColor;
                existingOverlay.style.opacity = overlayOpacity;
            } else {
                const overlay = document.createElement('div');
                overlay.className = 'chat-overlay';
                overlay.style.cssText = `
                    position: absolute;
                    top: 0;
                    left: 0;
                    right: 0;
                    bottom: 0;
                    background-color: ${overlayColor};
                    opacity: ${overlayOpacity};
                    pointer-events: none !important;
                    z-index: 0 !important;
                `;
                messagesArea.style.position = 'relative';
                messagesArea.prepend(overlay);
            }
        }
    }

    showThemeSelector() {
        const modal = document.getElementById('themeModal');
        if (modal) {
            modal.classList.add('show');
            this.populateThemeForm();
            this.enablePreviewMode();
        }
    }

    hideThemeSelector() {
        const modal = document.getElementById('themeModal');
        if (modal) {
            modal.classList.remove('show');
            this.disablePreviewMode();
        }
    }

    enablePreviewMode() {
        this.isPreviewMode = true;
        console.log('Preview mode enabled - drag to position image');
        this.showNotification('Preview mode: Drag to position background image', 'info');
    }

    disablePreviewMode() {
        this.isPreviewMode = false;
        this.isDragging = false;
        console.log('Preview mode disabled');
    }

    switchThemeTab(themeType) {
        // Update tabs
        document.querySelectorAll('.theme-tab').forEach(tab => {
            tab.classList.remove('active');
        });
        document.querySelector(`[data-theme-type="${themeType}"]`).classList.add('active');

        // Update panels
        document.querySelectorAll('.theme-panel').forEach(panel => {
            panel.classList.remove('active');
        });
        document.getElementById(`${themeType}Panel`).classList.add('active');
    }

    populateThemeForm() {
        if (!this.currentTheme) return;

        // Set theme type
        this.switchThemeTab(this.currentTheme.theme_type || 'solid');

        // Restore image position if available
        if (this.currentTheme.image_position_x !== undefined && this.currentTheme.image_position_y !== undefined) {
            this.imagePosition.x = this.currentTheme.image_position_x;
            this.imagePosition.y = this.currentTheme.image_position_y;
        }

        // Set solid colors
        if (this.currentTheme.light_color) {
            document.getElementById('lightColor').value = this.currentTheme.light_color;
            document.getElementById('lightColorText').value = this.currentTheme.light_color;
        }
        if (this.currentTheme.dark_color) {
            document.getElementById('darkColor').value = this.currentTheme.dark_color;
            document.getElementById('darkColorText').value = this.currentTheme.dark_color;
        }

        // Set gradient colors
        if (this.currentTheme.light_gradient_start) {
            document.getElementById('lightGradientStart').value = this.currentTheme.light_gradient_start;
            document.getElementById('lightGradientStartText').value = this.currentTheme.light_gradient_start;
        }
        if (this.currentTheme.light_gradient_end) {
            document.getElementById('lightGradientEnd').value = this.currentTheme.light_gradient_end;
            document.getElementById('lightGradientEndText').value = this.currentTheme.light_gradient_end;
        }
        if (this.currentTheme.dark_gradient_start) {
            document.getElementById('darkGradientStart').value = this.currentTheme.dark_gradient_start;
            document.getElementById('darkGradientStartText').value = this.currentTheme.dark_gradient_start;
        }
        if (this.currentTheme.dark_gradient_end) {
            document.getElementById('darkGradientEnd').value = this.currentTheme.dark_gradient_end;
            document.getElementById('darkGradientEndText').value = this.currentTheme.dark_gradient_end;
        }

        // Set gradient angle
        if (this.currentTheme.gradient_angle) {
            document.getElementById('gradientAngle').value = this.currentTheme.gradient_angle;
            document.getElementById('angleValue').textContent = this.currentTheme.gradient_angle;
        }

        // Set image fit
        if (this.currentTheme.image_fit) {
            document.getElementById('imageFit').value = this.currentTheme.image_fit;
        }

        // Set overlay settings
        if (this.currentTheme.overlay_opacity !== undefined) {
            document.getElementById('overlayOpacity').value = this.currentTheme.overlay_opacity * 100;
            document.getElementById('opacityValue').textContent = Math.round(this.currentTheme.overlay_opacity * 100);
        }
        if (this.currentTheme.light_overlay_color) {
            document.getElementById('lightOverlayColor').value = this.currentTheme.light_overlay_color;
            document.getElementById('lightOverlayColorText').value = this.currentTheme.light_overlay_color;
        }
        if (this.currentTheme.dark_overlay_color) {
            document.getElementById('darkOverlayColor').value = this.currentTheme.dark_overlay_color;
            document.getElementById('darkOverlayColorText').value = this.currentTheme.dark_overlay_color;
        }
    }

    async saveTheme() {
        console.log('=== SAVE THEME CALLED ===');
        console.log('currentConversationId:', this.currentConversationId);

        if (!this.currentConversationId) {
            console.error('No conversation ID');
            this.showNotification('No conversation ID', 'error');
            return;
        }

        const formData = this.collectFormData();
        console.log('formData:', formData);

        try {
            let response;
            let url;
            if (this.currentTheme && this.currentTheme.id) {
                // Update existing theme
                url = `/messaging/v1/themes/${this.currentTheme.id}/`;
                console.log('Updating existing theme at:', url);
                response = await fetch(url, {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': this.getCSRFToken()
                    },
                    body: JSON.stringify(formData)
                });
            } else {
                // Create new theme
                url = '/messaging/v1/themes/';
                console.log('Creating new theme at:', url);
                response = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': this.getCSRFToken()
                    },
                    body: JSON.stringify({
                        ...formData,
                        conversation: this.currentConversationId
                    })
                });
            }

            console.log('Response status:', response.status);
            console.log('Response ok:', response.ok);

            if (response.ok) {
                this.currentTheme = await response.json();
                console.log('Theme saved successfully:', this.currentTheme);
                this.applyTheme();
                this.hideThemeSelector();
                this.showNotification('Theme saved and applied successfully!');
            } else {
                const error = await response.json();
                console.error('Theme save error:', error);
                let errorMessage = 'Failed to save theme';
                
                if (error.detail) {
                    errorMessage = `Theme save failed: ${error.detail}`;
                } else if (error.error) {
                    errorMessage = `Theme save failed: ${error.error}`;
                } else if (typeof error === 'string') {
                    errorMessage = `Theme save failed: ${error}`;
                } else {
                    errorMessage = 'Theme save failed: Unknown error occurred';
                }
                
                this.showNotification(errorMessage, 'error');
            }
        } catch (error) {
            console.error('Failed to save theme:', error);
            this.showNotification(`Theme save failed: ${error.message}`, 'error');
        }
    }

    collectFormData() {
        const themeType = document.querySelector('.theme-tab.active').dataset.themeType;
        const formData = {
            theme_type: themeType,
            overlay_opacity: parseFloat(document.getElementById('overlayOpacity').value) / 100,
            light_overlay_color: document.getElementById('lightOverlayColor').value,
            dark_overlay_color: document.getElementById('darkOverlayColor').value,
            // Include image position for image themes
            image_position_x: this.imagePosition.x,
            image_position_y: this.imagePosition.y
        };

        if (themeType === 'solid') {
            formData.light_color = document.getElementById('lightColor').value;
            formData.dark_color = document.getElementById('darkColor').value;
        } else if (themeType === 'gradient') {
            formData.light_gradient_start = document.getElementById('lightGradientStart').value;
            formData.light_gradient_end = document.getElementById('lightGradientEnd').value;
            formData.dark_gradient_start = document.getElementById('darkGradientStart').value;
            formData.dark_gradient_end = document.getElementById('darkGradientEnd').value;
            formData.gradient_angle = parseInt(document.getElementById('gradientAngle').value);
        } else if (themeType === 'image') {
            formData.image_fit = document.getElementById('imageFit').value;
            // Include uploaded image data URLs
            if (this.uploadedImages.light) {
                formData.light_image = this.uploadedImages.light;
            }
            if (this.uploadedImages.dark) {
                formData.dark_image = this.uploadedImages.dark;
            }
        }

        return formData;
    }

    async resetTheme() {
        if (!this.currentConversationId) return;

        try {
            if (this.currentTheme && this.currentTheme.id) {
                await fetch(`/messaging/v1/themes/${this.currentTheme.id}/`, {
                    method: 'DELETE',
                    headers: {
                        'X-CSRFToken': this.getCSRFToken()
                    }
                });
            }

            this.currentTheme = null;
            this.applyDefaultTheme();
            this.hideThemeSelector();
            this.showNotification('Theme reset to default');
        } catch (error) {
            console.error('Failed to reset theme:', error);
            this.showNotification('Failed to reset theme', 'error');
        }
    }

    applyDefaultTheme() {
        // Reset CSS variables to defaults
        document.documentElement.style.setProperty('--chat-bg', '#f8fafc');
        document.documentElement.style.setProperty('--chat-bg-type', 'solid');
        document.documentElement.style.setProperty('--chat-overlay-color', 'transparent');
        document.documentElement.style.setProperty('--chat-overlay-opacity', '0');
        
        // Remove custom background from messages area
        const messagesArea = document.querySelector('.messages-area');
        if (messagesArea) {
            messagesArea.style.background = '';
            messagesArea.style.backgroundImage = ''
            const overlay = messagesArea.querySelector('.chat-overlay');
            if (overlay) overlay.remove();
        }
    }

    updatePreview() {
        // This would update a live preview in the modal
        // For now, we'll just update the actual chat area
        this.updatePreviewTheme();
    }

    updatePreviewTheme() {
        const formData = this.collectFormData();
        // Create temporary theme data for preview
        const previewTheme = {
            theme_type: formData.theme_type,
            css_variables_light: this.generateCSSVariables(formData, 'light'),
            css_variables_dark: this.generateCSSVariables(formData, 'dark'),
            overlay_light: { '--chat-overlay-color': formData.light_overlay_color, '--chat-overlay-opacity': formData.overlay_opacity },
            overlay_dark: { '--chat-overlay-color': formData.dark_overlay_color, '--chat-overlay-opacity': formData.overlay_opacity }
        };

        // Temporarily apply preview
        const originalTheme = this.currentTheme;
        this.currentTheme = previewTheme;
        this.applyTheme();
        this.currentTheme = originalTheme;
    }

    generateCSSVariables(formData, mode) {
        const vars = {};

        if (formData.theme_type === 'solid') {
            vars['--chat-bg'] = mode === 'light' ? formData.light_color : formData.dark_color;
            vars['--chat-bg-type'] = 'solid';
        } else if (formData.theme_type === 'gradient') {
            const start = mode === 'light' ? formData.light_gradient_start : formData.dark_gradient_start;
            const end = mode === 'light' ? formData.light_gradient_end : formData.dark_gradient_end;
            vars['--chat-bg'] = `linear-gradient(${formData.gradient_angle}deg, ${start}, ${end})`;
            vars['--chat-bg-type'] = 'gradient';
        } else if (formData.theme_type === 'image') {
            // For image type, the background image URL should be stored in the theme data
            // This will be set when the image is uploaded and saved
            const imageKey = mode === 'light' ? 'light_image' : 'dark_image';
            if (formData[imageKey]) {
                vars['--chat-bg'] = `url(${formData[imageKey]})`;
                vars['--chat-bg-type'] = 'image';
                vars['--chat-bg-fit'] = formData.image_fit || 'cover';
            }
        }

        return vars;
    }

    updateGradientPreview() {
        this.updatePreview();
    }

    updateOverlayPreview() {
        this.updatePreview();
    }

    getCSRFToken() {
        const cookies = document.cookie.split(';');
        for (let cookie of cookies) {
            const [name, value] = cookie.trim().split('=');
            if (name === 'csrftoken') return decodeURIComponent(value);
        }
        return '';
    }

    showNotification(message, type = 'success') {
        // Create a simple notification
        const notification = document.createElement('div');
        notification.className = `theme-notification ${type}`;
        notification.textContent = message;
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 12px 20px;
            background: ${type === 'success' ? 'var(--primary)' : '#dc3545'};
            color: white;
            border-radius: 8px;
            z-index: 10001;
            font-weight: 500;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        `;
        
        document.body.appendChild(notification);
        
        setTimeout(() => {
            notification.remove();
        }, 3000);
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.chatThemeManager = new ChatThemeManager();
});

// Export for potential use in other scripts
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ChatThemeManager;
}
