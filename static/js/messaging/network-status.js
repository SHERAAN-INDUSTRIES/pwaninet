/**
 * Network Status Detection for vasto Messaging
 * Handles online/offline detection and status management
 */

class NetworkStatus {
    constructor() {
        this.isOnline = navigator.onLine;
        this.listeners = [];
        this.offlineStartTime = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 1000; // Start with 1 second
        
        this.init();
    }

    init() {
        // Listen to browser online/offline events
        window.addEventListener('online', this.handleOnline.bind(this));
        window.addEventListener('offline', this.handleOffline.bind(this));
        
        // Listen to page visibility changes
        document.addEventListener('visibilitychange', this.handleVisibilityChange.bind(this));
        
        // Periodic connectivity check
        this.startConnectivityCheck();
        
        console.log('Network status initialized, online:', this.isOnline);
    }

    handleOnline() {
        if (!this.isOnline) {
            this.isOnline = true;
            this.offlineStartTime = null;
            this.reconnectAttempts = 0;
            console.log('Network connection restored');
            
            this.notifyListeners('online');
            this.hideOfflineBanner();
            this.attemptSync();
        }
    }

    handleOffline() {
        if (this.isOnline) {
            this.isOnline = false;
            this.offlineStartTime = Date.now();
            console.log('Network connection lost');
            
            this.notifyListeners('offline');
            this.showOfflineBanner();
        }
    }

    handleVisibilityChange() {
        // Check connectivity when page becomes visible
        if (!document.hidden && this.isOnline) {
            this.verifyConnectivity();
        }
    }

    async verifyConnectivity() {
        try {
            // Try to fetch a small resource to verify actual connectivity
            const response = await fetch('/static/images/favicon.ico', {
                method: 'HEAD',
                cache: 'no-cache',
                timeout: 5000
            });
            
            if (response.ok) {
                if (!this.isOnline) {
                    this.handleOnline();
                }
            } else {
                throw new Error('Network check failed');
            }
        } catch (error) {
            if (this.isOnline) {
                this.handleOffline();
            }
        }
    }

    startConnectivityCheck() {
        // Check connectivity every 30 seconds
        setInterval(() => {
            if (this.isOnline) {
                this.verifyConnectivity();
            }
        }, 30000);
    }

    addListener(callback) {
        this.listeners.push(callback);
    }

    removeListener(callback) {
        const index = this.listeners.indexOf(callback);
        if (index > -1) {
            this.listeners.splice(index, 1);
        }
    }

    notifyListeners(event, data = {}) {
        this.listeners.forEach(callback => {
            try {
                callback(event, { isOnline: this.isOnline, ...data });
            } catch (error) {
                console.error('Network status listener error:', error);
            }
        });
    }

    showOfflineBanner() {
        let banner = document.getElementById('offline-banner');
        
        if (!banner) {
            banner = this.createOfflineBanner();
            document.body.insertBefore(banner, document.body.firstChild);
        }
        
        banner.style.display = 'block';
        banner.setAttribute('aria-hidden', 'false');
    }

    hideOfflineBanner() {
        const banner = document.getElementById('offline-banner');
        if (banner) {
            banner.style.display = 'none';
            banner.setAttribute('aria-hidden', 'true');
        }
    }

    createOfflineBanner() {
        const banner = document.createElement('div');
        banner.id = 'offline-banner';
        banner.className = 'offline-banner';
        banner.setAttribute('role', 'alert');
        banner.setAttribute('aria-live', 'polite');
        banner.setAttribute('aria-hidden', 'true');
        
        banner.innerHTML = `
            <div class="offline-banner-content">
                <div class="offline-banner-icon">
                    <i aria-hidden="true" data-lucide="wifi-off" class="vasto-icon"></i>
                </div>
                <div class="offline-banner-text">
                    <strong>You're offline.</strong> Showing saved messages.
                </div>
                <div class="offline-banner-close" onclick="this.parentElement.parentElement.style.display='none'">
                    <i aria-hidden="true" data-lucide="x" class="vasto-icon"></i>
                </div>
            </div>
        `;
        
        // Add styles
        const style = document.createElement('style');
        style.textContent = `
            .offline-banner {
                display: none;
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                z-index: 9999;
                background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
                color: white;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                font-size: 14px;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
                animation: slideDown 0.3s ease-out;
            }
            
            .offline-banner-content {
                display: flex;
                align-items: center;
                padding: 12px 16px;
                max-width: 1200px;
                margin: 0 auto;
                gap: 12px;
            }
            
            .offline-banner-icon {
                flex-shrink: 0;
                font-size: 18px;
                opacity: 0.9;
            }
            
            .offline-banner-text {
                flex: 1;
                line-height: 1.4;
            }
            
            .offline-banner-close {
                flex-shrink: 0;
                width: 24px;
                height: 24px;
                display: flex;
                align-items: center;
                justify-content: center;
                border-radius: 50%;
                background: rgba(255, 255, 255, 0.2);
                cursor: pointer;
                transition: background-color 0.2s ease;
            }
            
            .offline-banner-close:hover {
                background: rgba(255, 255, 255, 0.3);
            }
            
            @keyframes slideDown {
                from {
                    transform: translateY(-100%);
                    opacity: 0;
                }
                to {
                    transform: translateY(0);
                    opacity: 1;
                }
            }
            
            /* Dark theme support */
            [data-theme="dark"] .offline-banner {
                background: linear-gradient(135deg, #d97706 0%, #b45309 100%);
            }
            
            /* Mobile adjustments */
            @media (max-width: 768px) {
                .offline-banner-content {
                    padding: 10px 12px;
                    font-size: 13px;
                }
                
                .offline-banner-icon {
                    font-size: 16px;
                }
                
                .offline-banner-close {
                    width: 20px;
                    height: 20px;
                    font-size: 12px;
                }
            }
        `;
        
        document.head.appendChild(style);
        return banner;
    }

    async attemptSync() {
        if (window.syncManager) {
            try {
                await window.syncManager.syncAll();
            } catch (error) {
                console.error('Sync attempt failed:', error);
            }
        }
    }

    getConnectionStatus() {
        return {
            isOnline: this.isOnline,
            offlineStartTime: this.offlineStartTime,
            reconnectAttempts: this.reconnectAttempts,
            connectionType: this.getConnectionType()
        };
    }

    getConnectionType() {
        // Get connection type if available
        if (navigator.connection) {
            return {
                type: navigator.connection.effectiveType,
                downlink: navigator.connection.downlink,
                rtt: navigator.connection.rtt,
                saveData: navigator.connection.saveData
            };
        }
        return null;
    }

    async waitForConnection(timeout = 30000) {
        return new Promise((resolve, reject) => {
            if (this.isOnline) {
                resolve(true);
                return;
            }
            
            const timeoutId = setTimeout(() => {
                this.removeListener(listener);
                reject(new Error('Connection timeout'));
            }, timeout);
            
            const listener = (event) => {
                if (event === 'online') {
                    clearTimeout(timeoutId);
                    this.removeListener(listener);
                    resolve(true);
                }
            };
            
            this.addListener(listener);
        });
    }
}

// Global instance
window.networkStatus = new NetworkStatus();
