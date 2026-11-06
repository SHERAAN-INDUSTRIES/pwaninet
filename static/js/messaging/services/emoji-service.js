/**
 * Emoji Service
 * Manages emoji picker functionality
 */

import { eventBus } from '../../chat/core/event-bus.js?v=20261106-group1';
import { EVENTS } from '../../chat/shared/constants.js?v=20261106-group1';

export class EmojiService {
  constructor() {
    this.emojis = [
      '😀', '😃', '😄', '😁', '😆', '😅', '😂', '🤣',
      '😊', '😇', '🙂', '🙃', '😉', '😌', '😍', '🥰',
      '😘', '😗', '😙', '😚', '😋', '😛', '😝', '😜',
      '🤪', '🤨', '🧐', '🤓', '😎', '🤩', '🥳', '😏',
      '😒', '😞', '😔', '😟', '😕', '🙁', '☹️', '😣',
      '😖', '😫', '😩', '🥺', '😢', '😭', '😤', '😠',
      '😡', '🤬', '👍', '👎', '👏', '🙌', '🤝', '❤️',
      '🧡', '💛', '💚', '💙', '💜', '🖤', '🤍', '💯',
      '🔥', '💢', '💥', '💫', '💦', '💨', '🕳️', '💣',
      '💬', '👁️', '👀', '👂', '👃', '👄', '👅', '🎃',
      '🎄', '🎆', '🎇', '🧨', '✨', '🎈', '🎉', '🎊',
      '🎋', '🎍', '🎎', '🎏', '🎐', '🎑', '🧧', '🎀',
      '🎁', '🎗️', '🎟️', '🎫', '🎖️', '🏆', '🏅', '🥇',
      '🥈', '🥉', '⚽', '⚾', '🥎', '🏀', '🏐', '🏈',
      '🏉', '🎾', '🥏', '🎳', '🏏', '🏑', '🏒', '🥍',
      '🏓', '🏸', '🥊', '🥋', '🥅', '⛳', '⛸️', '🎣',
      '🤿', '🎽', '🎿', '🛷', '🥌', '🎯', '🪀', '🪁',
      '🎱', '🔮', '🧿', '🎮', '🕹️', '🎰', '🎲', '🧩',
      '🧸', '♠️', '♥️', '♦️', '♣️', '♟️', '🃏', '🀄',
      '🎴', '🎭', '🖼️', '🎨', '🧵', '🧶', '👓', '🕶️',
      '🥽', '🥼', '🦺', '👔', '👕', '👖', '🧣', '🧤',
      '🧥', '🧦', '👗', '👘', '🥻', '🩱', '🩲', '🩳',
      '👙', '👚', '👛', '👜', '👝', '🛍️', '🎒', '👞',
      '👟', '🥾', '🥿', '👠', '👡', '🩰', '👢', '👑',
      '👒', '🎩', '🎓', '🧢', '⛑️', '📿', '💄', '💍',
      '💎', '🔇', '🔈', '🔉', '🔊', '📢', '📣', '📯',
      '🔔', '🔕', '🎼', '🎵', '🎶', '🎙️', '🎚️', '🎛️',
      '🎤', '🎧', '📻', '🎷', '🎸', '🎹', '🎺', '🎻',
      '🪕', '🥁', '📱', '📲', '☎️', '📞', '📟', '📠',
      '🔋', '🔌', '💻', '🖥️', '🖨️', '⌨️', '🖱️', '🖲️',
      '💽', '💾', '💿', '📀', '🧮', '🎥', '🎞️', '📽️',
      '🎬', '📺', '📷', '📸', '📹', '📼', '🔍', '🔎',
      '🕯️', '💡', '🔦', '🏮', '🪔', '📔', '📕', '📖',
      '📗', '📘', '📙', '📚', '📓', '📒', '📃', '📜',
      '📄', '📰', '🗞️', '📑', '🔖', '🏷️', '💰', '💴',
      '💵', '💶', '💷', '💸', '💳', '🧾', '💹', '✉️',
      '📧', '📨', '📩', '📤', '📥', '📦', '📫', '📪',
      '📬', '📭', '📮', '🗳️', '✏️', '✒️', '🖋️', '🖊️',
      '🖌️', '🖍️', '📝', '💼', '📁', '📂', '🗂️', '📅',
      '📆', '🗒️', '🗓️', '📇', '📈', '📉', '📊', '📋',
      '📌', '📍', '📎', '🖇️', '📏', '📐', '✂️', '🗃️',
      '🗄️', '🗑️', '🔒', '🔓', '🔏', '🔐', '🔑', '🗝️',
      '🔨', '🪓', '⛏️', '⚒️', '🛠️', '🗡️', '⚔️', '🔫',
      '🏹', '🛡️', '🔧', '🔩', '⚙️', '🗜️', '⚖️', '🦯',
      '🔗', '⛓️', '🧰', '🧲', '⚗️', '🧪', '🧫', '🧬',
      '🔬', '🔭', '📡', '💉', '🩸', '💊', '🩹', '🩺',
      '🚪', '🛗', '🪞', '🪟', '🛏️', '🛋️', '🪑', '🚽',
      '🪠', '🚿', '🛁', '🪤', '🪒', '🧴', '🧷', '🧹',
      '🧺', '🧻', '🧼', '🪥', '🧽', '🧯', '🛒', '🚬',
      '⚰️', '⚱️', '🗿', '🏧', '🚮', '🚰', '♿', '🚹',
      '🚺', '🚻', '🚼', '🚾', '🛂', '🛃', '🛄', '🛅',
      '⚠️', '🚸', '⛔', '🚫', '🚳', '🚭', '🚯', '🚱',
      '🚷', '📵', '🔞', '☢️', '☣️', '⬆️', '↗️', '➡️',
      '↘️', '⬇️', '↙️', '⬅️', '↖️', '↕️', '↔️', '↩️',
      '↪️', '⤴️', '⤵️', '🔃', '🔄', '🔙', '🔚', '🔛',
      '🔜', '🔝', '🛐', '⚛️', '🕉️', '✡️', '☸️', '☯️',
      '✝️', '☦️', '☪️', '☮️', '🕎', '🔯', '♈', '♉',
      '♊', '♋', '♌', '♍', '♎', '♏', '♐', '♑',
      '♒', '♓', '⛎', '🔀', '🔁', '🔂', '▶️', '⏩',
      '⏭️', '⏯️', '◀️', '⏪', '⏮️', '🔼', '⏫', '🔽',
      '⏬', '⏸️', '⏹️', '⏺️', '⏏️', '🎦', '🔅', '🔆',
      '📶', '📳', '📴', '♀️', '♂️', '⚕️', '♾️', '♻️',
      '⚜️', '🔱', '📛', '🔰', '⭕', '✅', '☑️', '✔️',
      '✖️', '❌', '❎', '➕', '➖', '➗', '➰', '➿',
      '〽️', '✳️', '✴️', '❇️', '‼️', '⁉️', '❓', '❔',
      '❕', '❗', '〰️', '©️', '®️', '™️', '#️⃣', '*️⃣',
      '0️⃣', '1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣',
      '8️⃣', '9️⃣', '🔟', '🔠', '🔡', '🔢', '🔣', '🔤',
      '🅰️', '🆎', '🅱️', '🆑', '🆒', '🆓', 'ℹ️', '🆔',
      'Ⓜ️', '🆕', '🆖', '🅾️', '🆗', '🅿️', '🆘', '🆙',
      '🆚', '🈁', '🈂️', '🈷️', '🈶', '🈯', '🉐', '🈹',
      '🈚', '🈲', '🉑', '🈸', '🈴', '🈳', '㊗️', '㊙️',
      '🈺', '🈵', '🔴', '🟠', '🟡', '🟢', '🔵', '🟣',
      '🟤', '⚫', '⚪', '🟥', '🟧', '🟩', '🟦', '🟪',
      '🟫', '⬛', '⬜', '◼️', '◻️', '◾', '◽', '▪️',
      '▫️', '🔶', '🔷', '🔸', '🔹', '🔺', '🔻', '💠',
      '🔘', '🔳', '🔲', '🏁', '🚩', '🎌', '🏴', '🏳️',
      '🏳️‍🌈', '🏴‍☠️'
    ];
    this.isOpen = false;
  }

  /**
   * Initialize emoji service
   */
  init() {
    this.setupEventListeners();
    this.renderEmojis();
  }

  /**
   * Setup event listeners
   */
  setupEventListeners() {
    // Listen for emoji picker toggle
    eventBus.on(EVENTS.EMOJI_PICKER_TOGGLE, (isOpen) => {
      this.isOpen = (isOpen !== undefined) ? isOpen : !this.isOpen;
    });

    // Listen for emoji insertion requests
    eventBus.on(EVENTS.EMOJI_INSERT, (emoji) => {
      this.insertEmoji(emoji);
    });
  }

  /**
   * Render emojis to the emoji picker
   */
  renderEmojis() {
    const emojiContent = document.getElementById('emojiContent');
    if (!emojiContent) return;

    emojiContent.innerHTML = '';

    this.emojis.forEach(emoji => {
      const emojiElement = document.createElement('div');
      emojiElement.className = 'emoji-item';
      emojiElement.textContent = emoji;
      emojiElement.addEventListener('click', () => {
        this.insertEmoji(emoji);
        this.closePicker();
      });
      emojiContent.appendChild(emojiElement);
    });
  }

  /**
   * Insert emoji into message input
   * @param {string} emoji - Emoji to insert
   */
  insertEmoji(emoji) {
    const messageInput = document.getElementById('messageInput');
    if (!messageInput) return;

    const currentValue = messageInput.value;
    const cursorPosition = messageInput.selectionStart;
    const newValue = currentValue.slice(0, cursorPosition) + emoji + currentValue.slice(cursorPosition);
    messageInput.value = newValue;

    const newCursorPosition = cursorPosition + emoji.length;
    messageInput.setSelectionRange(newCursorPosition, newCursorPosition);
    messageInput.focus();

    // Emit event for other listeners
    eventBus.emit(EVENTS.EMOJI_INSERT, emoji);
  }

  /**
   * Open emoji picker
   */
  openPicker() {
    const emojiPicker = document.getElementById('emojiPicker');
    const overlay = document.getElementById('overlay');
    
    if (emojiPicker) emojiPicker.classList.add('show');
    if (overlay) overlay.classList.add('show');
    
    this.isOpen = true;
    eventBus.emit(EVENTS.EMOJI_PICKER_TOGGLE, true);
  }

  /**
   * Close picker
   */
  closePicker() {
    const emojiPicker = document.getElementById('emojiPicker');
    const overlay = document.getElementById('overlay');
    
    if (emojiPicker) emojiPicker.classList.remove('show');
    if (overlay) overlay.classList.remove('show');
    
    this.isOpen = false;
    eventBus.emit(EVENTS.EMOJI_PICKER_TOGGLE, false);
  }

  /**
   * Toggle emoji picker
   */
  togglePicker() {
    if (this.isOpen) {
      this.closePicker();
    } else {
      this.openPicker();
    }
  }

  /**
   * Get all emojis
   * @returns {Array} Array of emojis
   */
  getEmojis() {
    return [...this.emojis];
  }

  /**
   * Search emojis
   * @param {string} query - Search query
   * @returns {Array} Filtered emojis
   */
  searchEmojis(query) {
    if (!query) return this.emojis;
    return this.emojis.filter(emoji => emoji.includes(query));
  }
}

// Create global instance
export const emojiService = new EmojiService();
