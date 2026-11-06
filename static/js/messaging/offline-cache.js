/**
 * Offline Cache Management for vasto Messaging
 * Handles IndexedDB storage for conversations, messages, and outbox
 */

class OfflineCache {
    constructor() {
        this.owner = String(window.vastoUserId || document.body?.dataset.userId || '');
        this.dbName = '';
        this.initializing = null;
        this.databases = new Map();
        this.pendingDatabases = new Map();
        this.blocked = false;
        navigator.serviceWorker?.addEventListener('message', event => {
            if (event.data?.type === 'PRIVATE_OFFLINE_INVALIDATED' && event.data.owner !== this.owner) {
                this.blocked = true; this.databases.forEach(db => db.close()); this.databases.clear(); this.db = null;
            }
        });
        this.dbVersion = 1;
        this.db = null;
    }

    async init(scope = window.IS_GROUP_CHAT ? 'group' : 'direct') {
        if (!this.owner || this.blocked) throw new Error('Sign in to access saved messages');
        const name = `vasto_chat_v2_${this.owner}_${scope}`;
        if (this.databases.has(name)) { this.dbName = name; this.db = this.databases.get(name); return this.db; }
        if (!this.pendingDatabases.has(name)) this.pendingDatabases.set(name, this.openDatabase(name));
        try {
            const db = await this.pendingDatabases.get(name);
            if (this.blocked) { db.close(); throw new Error('Account changed'); }
            this.databases.set(name, db);
            this.dbName = name; this.db = db;
            db.onversionchange = () => { db.close(); this.databases.delete(name); if (this.db === db) this.db = null; };
            return db;
        } finally { this.pendingDatabases.delete(name); }
    }

    async openDatabase(name) {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(name, this.dbVersion);

            request.onerror = () => reject(request.error);
            request.onsuccess = () => resolve(request.result);

            request.onupgradeneeded = (event) => {
                const db = event.target.result;

                // Create object stores
                if (!db.objectStoreNames.contains('conversations')) {
                    const conversationStore = db.createObjectStore('conversations', { keyPath: 'id' });
                    conversationStore.createIndex('updated_at', 'updated_at');
                    conversationStore.createIndex('participant_id', 'participant_id');
                }

                if (!db.objectStoreNames.contains('messages')) {
                    const messageStore = db.createObjectStore('messages', { keyPath: 'id' });
                    messageStore.createIndex('conversation_id', 'conversation_id');
                    messageStore.createIndex('timestamp', 'timestamp');
                    messageStore.createIndex('created_at', 'created_at');
                }

                if (!db.objectStoreNames.contains('outbox')) {
                    const outboxStore = db.createObjectStore('outbox', { keyPath: 'id', autoIncrement: true });
                    outboxStore.createIndex('conversation_id', 'conversation_id');
                    outboxStore.createIndex('created_at', 'created_at');
                }

                if (!db.objectStoreNames.contains('users')) {
                    const userStore = db.createObjectStore('users', { keyPath: 'id' });
                    userStore.createIndex('username', 'username');
                }
            };
        });
    }

    // CONVERSATIONS
    async saveConversation(conversation) {
        await this.init();
        if (!this.db) return;
        
        const transaction = this.db.transaction(['conversations'], 'readwrite');
        const store = transaction.objectStore('conversations');
        
        // Add cache timestamp
        conversation.cached_at = new Date().toISOString();
        
        return store.put(conversation);
    }

    async saveConversations(conversations) {
        await this.init();
        if (!this.db || !conversations || !conversations.length) return;
        
        const transaction = this.db.transaction(['conversations'], 'readwrite');
        const store = transaction.objectStore('conversations');
        
        for (const item of conversations) {
            const conv = item.conversation || item;
            if (conv && conv.id) {
                const dataToSave = {
                    id: conv.id,
                    type: conv.type || 'direct',
                    name: conv.name || '',
                    is_pinned: item.is_pinned || false,
                    last_msg_time: conv.last_msg_time || null,
                    preview: item.preview || null,
                    updated_at: conv.updated_at || conv.last_msg_time || new Date().toISOString(),
                    cached_at: new Date().toISOString()
                };
                store.put(dataToSave);
            }
        }
        
        return transaction.complete;
    }

    async getConversations(limit = 50) {
        await this.init();
        if (!this.db) return [];
        
        const transaction = this.db.transaction(['conversations'], 'readonly');
        const store = transaction.objectStore('conversations');
        const index = store.index('updated_at');
        
        return new Promise((resolve, reject) => {
            const request = index.openCursor(null, 'prev');
            const results = [];
            
            request.onsuccess = (event) => {
                const cursor = event.target.result;
                if (cursor && results.length < limit) {
                    results.push(cursor.value);
                    cursor.continue();
                } else {
                    resolve(results);
                }
            };
            
            request.onerror = () => reject(request.error);
        });
    }

    async getConversation(conversationId) {
        await this.init();
        if (!this.db) return null;
        
        const transaction = this.db.transaction(['conversations'], 'readonly');
        const store = transaction.objectStore('conversations');
        
        return new Promise((resolve, reject) => {
            const request = store.get(conversationId);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    // MESSAGES
    async saveMessage(message) {
        return this.saveMessages([message]);
    }

    async saveMessages(messages) {
        const scope = messages?.[0]?.group != null ? 'group' : messages?.[0]?.conversation != null ? 'direct' : window.IS_GROUP_CHAT ? 'group' : 'direct';
        const db = await this.init(scope);
        if (!messages?.length) return;
        const now = new Date().toISOString();
        await new Promise((resolve, reject) => {
            const tx = db.transaction('messages', 'readwrite');
            const store = tx.objectStore('messages');
            for (const original of messages) {
                if (!original.id || !/^\d+$/.test(String(original.id))) continue;
                const message = { ...original, cached_at: now };
                if (message.is_deleted) { message.content = ''; message.encrypted_content = ''; message.attachments = []; message.attachment_url = null; }
                message.conversation_id = Number(message.conversation_id ?? message.conversation ?? message.conversationId ?? message.group);
                if (!Number.isFinite(message.conversation_id)) continue;
                store.put(message);
            }
            tx.oncomplete = resolve;
            tx.onerror = tx.onabort = () => reject(tx.error || new Error('Message cache interrupted'));
        });
        await this.pruneMessages(db);
    }

    async pruneMessages(db) {
        await new Promise((resolve, reject) => {
            const tx = db.transaction('messages', 'readwrite');
            const store = tx.objectStore('messages');
            const request = store.getAll();
            request.onsuccess = () => {
                const records = request.result.sort((a, b) => new Date(b.created_at || b.cached_at) - new Date(a.created_at || a.cached_at));
                let bytes = 0;
                const counts = new Map();
                records.forEach((item, index) => {
                    bytes += JSON.stringify(item).length * 2;
                    const count = (counts.get(item.conversation_id) || 0) + 1;
                    counts.set(item.conversation_id, count);
                    if (index >= 10000 || bytes > 30 * 1024 * 1024 || count > 1000 || Date.now() - new Date(item.cached_at).getTime() > 30 * 86400000) store.delete(item.id);
                });
            };
            tx.oncomplete = resolve;
            tx.onerror = () => reject(tx.error);
        });
    }

    async getMessages(conversationId, limit = 1000, offset = 0) {
        const db = await this.init();
        return new Promise((resolve, reject) => {
            const req = db.transaction('messages').objectStore('messages').index('conversation_id').getAll(Number(conversationId));
            req.onsuccess = () => {
                const records = req.result.filter(item => Date.now() - new Date(item.cached_at).getTime() <= 30 * 86400000)
                    .sort((a, b) => new Date(a.created_at || a.timestamp) - new Date(b.created_at || b.timestamp) || Number(a.id) - Number(b.id));
                resolve(records.slice(Math.max(0, records.length - offset - Math.min(limit, 1000)), offset ? -offset : undefined));
            };
            req.onerror = () => reject(req.error);
        });
    }

    async deleteMessages(ids) {
        const db = await this.init();
        await new Promise((resolve, reject) => {
            const tx = db.transaction('messages', 'readwrite');
            ids.forEach(id => tx.objectStore('messages').delete(id));
            tx.oncomplete = resolve; tx.onerror = () => reject(tx.error);
        });
    }

    async getMessage(messageId) {
        await this.init();
        if (!this.db) return null;
        
        const transaction = this.db.transaction(['messages'], 'readonly');
        const store = transaction.objectStore('messages');
        
        return new Promise((resolve, reject) => {
            const request = store.get(messageId);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    // OUTBOX (pending messages)
    async saveToOutbox(message) {
        await this.init();
        if (!this.db) return;
        
        const transaction = this.db.transaction(['outbox'], 'readwrite');
        const store = transaction.objectStore('outbox');
        
        const outboxMessage = {
            ...message,
            created_at: new Date().toISOString(),
            status: 'pending'
        };
        
        return store.add(outboxMessage);
    }

    async getOutboxMessages(conversationId = null) {
        await this.init();
        if (!this.db) return [];
        
        const transaction = this.db.transaction(['outbox'], 'readonly');
        const store = transaction.objectStore('outbox');
        
        return new Promise((resolve, reject) => {
            let request;
            if (conversationId) {
                const index = store.index('conversation_id');
                request = index.openCursor(IDBKeyRange.only(conversationId));
            } else {
                request = store.openCursor();
            }
            
            const results = [];
            
            request.onsuccess = (event) => {
                const cursor = event.target.result;
                if (cursor) {
                    results.push(cursor.value);
                    cursor.continue();
                } else {
                    resolve(results);
                }
            };
            
            request.onerror = () => reject(request.error);
        });
    }

    async removeFromOutbox(outboxId) {
        await this.init();
        if (!this.db) return;
        
        const transaction = this.db.transaction(['outbox'], 'readwrite');
        const store = transaction.objectStore('outbox');
        
        return new Promise((resolve, reject) => {
            const request = store.delete(outboxId);
            request.onsuccess = () => resolve();
            request.onerror = () => reject(request.error);
        });
    }

    // USERS
    async saveUser(user) {
        await this.init();
        if (!this.db) return;
        
        const transaction = this.db.transaction(['users'], 'readwrite');
        const store = transaction.objectStore('users');
        
        user.cached_at = new Date().toISOString();
        
        return store.put(user);
    }

    async getUser(userId) {
        await this.init();
        if (!this.db) return null;
        
        const transaction = this.db.transaction(['users'], 'readonly');
        const store = transaction.objectStore('users');
        
        return new Promise((resolve, reject) => {
            const request = store.get(userId);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    // UTILITY
    async clearCache() {
        await this.init();
        if (!this.db) return;
        
        const stores = ['conversations', 'messages', 'outbox', 'users'];
        
        for (const storeName of stores) {
            const transaction = this.db.transaction([storeName], 'readwrite');
            const store = transaction.objectStore(storeName);
            store.clear();
        }
        
        console.log('Offline cache cleared');
    }

    async getCacheSize() {
        await this.init();
        if (!this.db) return 0;
        
        let totalSize = 0;
        const stores = ['conversations', 'messages', 'outbox', 'users'];
        
        for (const storeName of stores) {
            const transaction = this.db.transaction([storeName], 'readonly');
            const store = transaction.objectStore(storeName);
            
            const count = await new Promise((resolve) => {
                const request = store.count();
                request.onsuccess = () => resolve(request.result);
            });
            
            totalSize += count;
        }
        
        return totalSize;
    }
}

// Global instance
window.offlineCache = new OfflineCache();
window.offlineCache.init();
