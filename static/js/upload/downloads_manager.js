/**
 * Compatibility facade for static/js/upload/downloads_manager.js
 * Delegates to the unified Download Manager in static/js/downloads/
 */

import { downloadManager } from '../downloads/download_manager.js?v=20261106-group1';
import { downloadStorage } from '../downloads/download_storage.js?v=20261106-group1';
import { downloadQueue } from '../downloads/download_queue.js?v=20261106-group1';

export { downloadManager, downloadStorage, downloadQueue };
export const downloadsManager = downloadManager;
export default downloadManager;
