/**
 * Theme Service
 * Manages chat theme functionality including loading, saving, and applying themes
 */

import { messagingAPI } from '../../shared/api/messaging-api.js?v=20261106-group1';

export class ThemeService {
  constructor() {
    this.currentTheme = null;
    this.cacheKey = null;
  }

  /**
   * Initialize theme service for a conversation
   * @param {number} conversationId - Conversation ID
   */
  init(conversationId) {
    this.cacheKey = `chatTheme_${conversationId}`;
    this.loadTheme(conversationId);
  }

  /**
   * Load theme from database or localStorage
   * @param {number} conversationId - Conversation ID
   */
  async loadTheme(conversationId) {
    try {
      const data = await messagingAPI.getTheme(conversationId);
      const existingTheme = data.results?.[0];

      if (existingTheme) {
        console.log('Found existing theme in DB:', existingTheme);
        this.applyThemeFromData(existingTheme);
      } else {
        console.log('No theme found in DB, checking localStorage...');
        const savedTheme = localStorage.getItem(this.cacheKey);
        if (savedTheme) {
          this.applyThemeFromName(savedTheme);
        }
      }
    } catch (err) {
      console.error('Failed to load theme from DB:', err);
      const savedTheme = localStorage.getItem(this.cacheKey);
      if (savedTheme) {
        this.applyThemeFromName(savedTheme);
      }
    }
  }

  /**
   * Apply theme from database data
   * @param {object} themeData - Theme data from database
   */
  applyThemeFromData(themeData) {
    const messagesArea = document.getElementById('messagesContainer');
    if (!messagesArea) return;

    if (themeData.theme_type === 'solid') {
      if (themeData.light_color === '#f8fafc') {
        messagesArea.style.background = '#f8fafc';
        messagesArea.style.backgroundImage = 'none';
      } else if (themeData.dark_color === '#1f2937') {
        messagesArea.style.background = '#1f2937';
        messagesArea.style.backgroundImage = 'none';
      }
    } else if (themeData.theme_type === 'gradient') {
      const start = themeData.light_gradient_start;
      const end = themeData.light_gradient_end;
      const angle = themeData.gradient_angle || 135;
      messagesArea.style.backgroundImage = `linear-gradient(${angle}deg, ${start}, ${end})`;
    } else if (themeData.theme_type === 'image' && themeData.light_image_url) {
      messagesArea.style.backgroundImage = `url(${themeData.light_image_url})`;
      messagesArea.style.backgroundSize = 'cover';
      messagesArea.style.backgroundPosition = 'center';
    }
    console.log('✅ Theme applied from database');
  }

  /**
   * Apply theme by name (for localStorage themes)
   * @param {string} themeName - Theme name
   */
  applyThemeFromName(themeName) {
    const messagesArea = document.getElementById('messagesContainer');
    if (!messagesArea) return;

    const themeMap = {
      'light': () => {
        messagesArea.style.background = '#f8fafc';
        messagesArea.style.backgroundImage = 'none';
      },
      'dark': () => {
        messagesArea.style.background = '#1f2937';
        messagesArea.style.backgroundImage = 'none';
      },
      'wallpaper-1': () => {
        messagesArea.style.backgroundImage = 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)';
      },
      'wallpaper-2': () => {
        messagesArea.style.backgroundImage = 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)';
      },
      'wallpaper-3': () => {
        messagesArea.style.backgroundImage = 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)';
      },
      'wallpaper-4': () => {
        messagesArea.style.backgroundImage = 'linear-gradient(135deg, #43e97b 0%, #38f9d7 100%)';
      },
      'wallpaper-5': () => {
        messagesArea.style.backgroundImage = 'linear-gradient(135deg, #fa709a 0%, #fee140 100%)';
      },
      'wallpaper-6': () => {
        messagesArea.style.backgroundImage = 'linear-gradient(135deg, #30cfd0 0%, #330867 100%)';
      }
    };

    if (themeMap[themeName]) {
      themeMap[themeName]();
      console.log('✅ Theme applied from localStorage:', themeName);
    }
  }

  /**
   * Save theme to database and localStorage
   * @param {number} conversationId - Conversation ID
   * @param {object} themeData - Theme data to save
   * @param {string} themeName - Theme name for localStorage
   */
  async saveTheme(conversationId, themeData, themeName) {
    try {
      const result = await messagingAPI.saveTheme({
        conversation: conversationId,
        ...themeData
      });

      console.log('Theme saved to database:', result);

      // Update cache
      this.updateThemeCache(conversationId, themeName);

      // Also store in localStorage (per-conversation)
      localStorage.setItem(this.cacheKey, themeName);

      return { success: true, result };
    } catch (error) {
      console.error('Failed to save theme to database:', error);
      
      // Fall back to localStorage only (per-conversation)
      localStorage.setItem(this.cacheKey, themeName);
      
      return { success: false, error, fallback: true };
    }
  }

  /**
   * Update theme cache
   * @param {number} conversationId - Conversation ID
   * @param {string} themeName - Theme name
   */
  updateThemeCache(conversationId, themeName) {
    const cacheKey = `chatTheme_${conversationId}_theme`;
    const timestampKey = `chatTheme_${conversationId}_theme_timestamp`;
    
    localStorage.setItem(cacheKey, themeName);
    localStorage.setItem(timestampKey, Date.now().toString());
    
    console.log('✅ Theme cache updated for conversation:', conversationId);
  }

  /**
   * Get current theme
   * @returns {string|null} Current theme name
   */
  getCurrentTheme() {
    return localStorage.getItem(this.cacheKey);
  }

  /**
   * Clear theme cache for conversation
   * @param {number} conversationId - Conversation ID
   */
  clearCache(conversationId) {
    const cacheKey = `chatTheme_${conversationId}_theme`;
    const timestampKey = `chatTheme_${conversationId}_theme_timestamp`;
    const localStorageKey = `chatTheme_${conversationId}`;
    
    localStorage.removeItem(cacheKey);
    localStorage.removeItem(timestampKey);
    localStorage.removeItem(localStorageKey);
    
    console.log('🧹 Theme cache cleared for conversation:', conversationId);
  }
}

// Create global instance
export const themeService = new ThemeService();
