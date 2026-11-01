/**
 * TimetableCropper - Dedicated Fullscreen Cropping Composer for Academic Timetables.
 * Reuses the cropping mathematics, multi-touch handles, rule-of-thirds grid,
 * and aspect ratio snapping from Vasto chat's CroppingEngine.
 */

(function(window, document) {
    'use strict';

    class TimetableCroppingEngine {
        constructor() {
            this.overlay = document.getElementById('timetableCropOverlay');
            this.imgElement = document.getElementById('timetablePreviewImg');
            this.imgWrap = document.getElementById('timetablePreviewImageWrap');
            this.previewContainer = document.getElementById('timetablePreviewContainer');
            this.processingScrim = document.getElementById('timetableProcessingScrim');

            // Buttons & Dock
            this.closeBtn = document.getElementById('timetableCropCloseBtn');
            this.cancelBtn = document.getElementById('timetableCropCancelBtn');
            this.doneBtn = document.getElementById('timetableCropDoneBtn');
            this.rotateBtn = document.getElementById('timetableCropRotateBtn');
            this.aspectPills = document.querySelectorAll('#timetableCropAspectPills .crop-aspect-pill');

            // State
            this.currentFile = null;
            this.originalUrl = null;
            this.isActive = false;
            this.rotation = 0; // 0, 90, 180, 270
            this.currentAspect = 'free';
            this.cropRect = { x: 0.05, y: 0.05, width: 0.9, height: 0.9 };

            // Dynamic crop elements
            this.cropOverlayEl = null;
            this.cropBoxEl = null;
            this.masks = {};
            this.imageBounds = null;

            // Drag tracking
            this.isDragging = false;
            this.dragType = null;
            this.dragStart = { x: 0, y: 0 };
            this.initialCropRect = null;

            this._boundOnPointerMove = this._onPointerMove.bind(this);
            this._boundOnPointerUp = this._onPointerUp.bind(this);

            this._initEvents();
        }

        _initEvents() {
            if (this.closeBtn) {
                this.closeBtn.addEventListener('click', () => this.close());
            }
            if (this.cancelBtn) {
                this.cancelBtn.addEventListener('click', () => this.close());
            }
            if (this.rotateBtn) {
                this.rotateBtn.addEventListener('click', () => this.rotate());
            }
            if (this.doneBtn) {
                this.doneBtn.addEventListener('click', () => this.processAndUpload());
            }

            this.aspectPills.forEach(pill => {
                pill.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const aspect = pill.dataset.aspect || 'free';
                    this.setAspectRatio(aspect);
                });
            });

            window.addEventListener('resize', () => {
                if (this.isActive) {
                    this._updateOverlayGeometry();
                }
            });
        }

        /**
         * Open fullscreen cropping composer with selected image file
         */
        open(file, options = {}) {
            if (!file) return;

            this.currentFile = file;
            this.options = options; // { programmeId, yearNum, semNum, onComplete }
            this.rotation = 0;
            this.currentAspect = 'free';
            this.cropRect = { x: 0.05, y: 0.05, width: 0.9, height: 0.9 };

            if (this.originalUrl) {
                URL.revokeObjectURL(this.originalUrl);
            }
            this.originalUrl = URL.createObjectURL(file);

            if (this.processingScrim) {
                this.processingScrim.classList.add('d-none');
            }

            this.imgElement.src = this.originalUrl;
            this.imgElement.style.transform = 'none';

            this.overlay.style.display = 'block';
            requestAnimationFrame(() => {
                this.overlay.classList.add('show');
            });

            this.imgElement.onload = () => {
                this.isActive = true;
                this._ensureOverlayElements();
                this._updateOverlayGeometry();
                this._updateAspectPillsUI('free');
            };
        }

        close() {
            this.isActive = false;
            this.overlay.classList.remove('show');
            setTimeout(() => {
                this.overlay.style.display = 'none';
                if (this.cropOverlayEl) {
                    this.cropOverlayEl.remove();
                    this.cropOverlayEl = null;
                }
                if (this.originalUrl) {
                    URL.revokeObjectURL(this.originalUrl);
                    this.originalUrl = null;
                }
                this.currentFile = null;
            }, 200);
        }

        rotate() {
            this.rotation = (this.rotation + 90) % 360;
            this.imgElement.style.transform = `rotate(${this.rotation}deg)`;
            setTimeout(() => {
                this._updateOverlayGeometry();
            }, 50);
        }

        setAspectRatio(aspect) {
            this.currentAspect = aspect;
            this._updateAspectPillsUI(aspect);
            this._fitAspectToRect(aspect);
            this._renderCropBox();
        }

        _updateAspectPillsUI(aspect) {
            this.aspectPills.forEach(pill => {
                if (pill.dataset.aspect === aspect) {
                    pill.classList.add('active');
                } else {
                    pill.classList.remove('active');
                }
            });
        }

        _fitAspectToRect(aspect) {
            if (aspect === 'free' || !this.imageBounds) return;

            let targetRatio = 1;
            if (aspect === '1:1') targetRatio = 1;
            else if (aspect === '4:3') targetRatio = 4 / 3;
            else if (aspect === '16:9') targetRatio = 16 / 9;

            const isRotatedSideways = (this.rotation === 90 || this.rotation === 270);
            const imgWidth = isRotatedSideways ? (this.imageBounds.height || 1) : (this.imageBounds.width || 1);
            const imgHeight = isRotatedSideways ? (this.imageBounds.width || 1) : (this.imageBounds.height || 1);
            const imgAspect = imgWidth / imgHeight;

            const normRatio = targetRatio / imgAspect;

            let w = 0.85;
            let h = w / normRatio;

            if (h > 0.85) {
                h = 0.85;
                w = h * normRatio;
            }

            w = Math.max(0.15, Math.min(0.95, w));
            h = Math.max(0.15, Math.min(0.95, h));

            const x = (1 - w) / 2;
            const y = (1 - h) / 2;

            this.cropRect = { x, y, width: w, height: h };
        }

        _ensureOverlayElements() {
            if (this.cropOverlayEl) {
                this.cropOverlayEl.remove();
            }

            const overlay = document.createElement('div');
            overlay.className = 'media-crop-overlay';

            // 4 Directional Backdrop Masks
            const maskTop = document.createElement('div');
            maskTop.className = 'crop-backdrop-mask mask-top';
            const maskBottom = document.createElement('div');
            maskBottom.className = 'crop-backdrop-mask mask-bottom';
            const maskLeft = document.createElement('div');
            maskLeft.className = 'crop-backdrop-mask mask-left';
            const maskRight = document.createElement('div');
            maskRight.className = 'crop-backdrop-mask mask-right';

            overlay.appendChild(maskTop);
            overlay.appendChild(maskBottom);
            overlay.appendChild(maskLeft);
            overlay.appendChild(maskRight);

            // Center Highlighted Crop Box with Handles
            const cropBox = document.createElement('div');
            cropBox.className = 'media-crop-box';
            cropBox.innerHTML = `
                <div class="crop-grid-line grid-h1"></div>
                <div class="crop-grid-line grid-h2"></div>
                <div class="crop-grid-line grid-v1"></div>
                <div class="crop-grid-line grid-v2"></div>
                <div class="crop-handle handle-nw" data-handle="nw"></div>
                <div class="crop-handle handle-ne" data-handle="ne"></div>
                <div class="crop-handle handle-se" data-handle="se"></div>
                <div class="crop-handle handle-sw" data-handle="sw"></div>
                <div class="crop-handle handle-n" data-handle="n"></div>
                <div class="crop-handle handle-s" data-handle="s"></div>
                <div class="crop-handle handle-w" data-handle="w"></div>
                <div class="crop-handle handle-e" data-handle="e"></div>
            `;

            overlay.appendChild(cropBox);
            this.imgWrap.appendChild(overlay);

            this.cropOverlayEl = overlay;
            this.cropBoxEl = cropBox;
            this.masks = { top: maskTop, bottom: maskBottom, left: maskLeft, right: maskRight };

            this._setupDragListeners();
        }

        _updateOverlayGeometry() {
            if (!this.imgElement || !this.cropOverlayEl) return;

            const wrapRect = this.imgWrap.getBoundingClientRect();
            const imgRect = this.imgElement.getBoundingClientRect();

            const left = imgRect.left - wrapRect.left;
            const top = imgRect.top - wrapRect.top;
            const width = imgRect.width;
            const height = imgRect.height;

            this.cropOverlayEl.style.position = 'absolute';
            this.cropOverlayEl.style.left = `${left}px`;
            this.cropOverlayEl.style.top = `${top}px`;
            this.cropOverlayEl.style.width = `${width}px`;
            this.cropOverlayEl.style.height = `${height}px`;

            this.imageBounds = { left, top, width, height };
            this._renderCropBox();
        }

        _renderCropBox() {
            if (!this.cropBoxEl || !this.masks.top) return;

            const { x, y, width, height } = this.cropRect;

            const leftPct = x * 100;
            const topPct = y * 100;
            const widthPct = width * 100;
            const heightPct = height * 100;

            this.cropBoxEl.style.left = `${leftPct}%`;
            this.cropBoxEl.style.top = `${topPct}%`;
            this.cropBoxEl.style.width = `${widthPct}%`;
            this.cropBoxEl.style.height = `${heightPct}%`;

            this.masks.top.style.top = '0';
            this.masks.top.style.left = '0';
            this.masks.top.style.right = '0';
            this.masks.top.style.height = `${topPct}%`;

            this.masks.bottom.style.top = `${topPct + heightPct}%`;
            this.masks.bottom.style.left = '0';
            this.masks.bottom.style.right = '0';
            this.masks.bottom.style.bottom = '0';

            this.masks.left.style.top = `${topPct}%`;
            this.masks.left.style.height = `${heightPct}%`;
            this.masks.left.style.left = '0';
            this.masks.left.style.width = `${leftPct}%`;

            this.masks.right.style.top = `${topPct}%`;
            this.masks.right.style.height = `${heightPct}%`;
            this.masks.right.style.left = `${leftPct + widthPct}%`;
            this.masks.right.style.right = '0';
        }

        _setupDragListeners() {
            // Drag crop box (move)
            this.cropBoxEl.addEventListener('pointerdown', (e) => {
                if (e.target.classList.contains('crop-handle')) return;
                e.preventDefault();
                e.stopPropagation();
                this._startDrag('move', e);
            });

            // Drag handles (resize)
            const handles = this.cropBoxEl.querySelectorAll('.crop-handle');
            handles.forEach(handle => {
                handle.addEventListener('pointerdown', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const handleType = handle.dataset.handle;
                    this._startDrag(handleType, e);
                });
            });
        }

        _startDrag(type, e) {
            this.isDragging = true;
            this.dragType = type;
            this.dragStart = { x: e.clientX, y: e.clientY };
            this.initialCropRect = { ...this.cropRect };

            window.addEventListener('pointermove', this._boundOnPointerMove);
            window.addEventListener('pointerup', this._boundOnPointerUp);
            window.addEventListener('pointercancel', this._boundOnPointerUp);
        }

        _onPointerMove(e) {
            if (!this.isDragging || !this.imageBounds) return;

            const dx = (e.clientX - this.dragStart.x) / this.imageBounds.width;
            const dy = (e.clientY - this.dragStart.y) / this.imageBounds.height;

            const init = this.initialCropRect;
            let { x, y, width, height } = init;

            const minSize = 0.1;

            if (this.dragType === 'move') {
                x = Math.max(0, Math.min(1 - width, init.x + dx));
                y = Math.max(0, Math.min(1 - height, init.y + dy));
            } else {
                // Resize corners and edges
                let newLeft = init.x;
                let newTop = init.y;
                let newRight = init.x + init.width;
                let newBottom = init.y + init.height;

                if (this.dragType.includes('w')) {
                    newLeft = Math.max(0, Math.min(newRight - minSize, init.x + dx));
                }
                if (this.dragType.includes('e')) {
                    newRight = Math.min(1, Math.max(newLeft + minSize, init.x + init.width + dx));
                }
                if (this.dragType.includes('n')) {
                    newTop = Math.max(0, Math.min(newBottom - minSize, init.y + dy));
                }
                if (this.dragType.includes('s')) {
                    newBottom = Math.min(1, Math.max(newTop + minSize, init.y + init.height + dy));
                }

                x = newLeft;
                y = newTop;
                width = newRight - newLeft;
                height = newBottom - newTop;

                // Aspect ratio constraint
                if (this.currentAspect !== 'free') {
                    let targetRatio = 1;
                    if (this.currentAspect === '1:1') targetRatio = 1;
                    else if (this.currentAspect === '4:3') targetRatio = 4 / 3;
                    else if (this.currentAspect === '16:9') targetRatio = 16 / 9;

                    const isRotatedSideways = (this.rotation === 90 || this.rotation === 270);
                    const imgW = isRotatedSideways ? this.imageBounds.height : this.imageBounds.width;
                    const imgH = isRotatedSideways ? this.imageBounds.width : this.imageBounds.height;
                    const imgAspect = imgW / imgH;
                    const normRatio = targetRatio / imgAspect;

                    // Constrain height based on width
                    height = width / normRatio;
                    if (y + height > 1) {
                        height = 1 - y;
                        width = height * normRatio;
                    }
                }
            }

            this.cropRect = {
                x: Math.max(0, Math.min(1 - width, x)),
                y: Math.max(0, Math.min(1 - height, y)),
                width: Math.max(minSize, Math.min(1, width)),
                height: Math.max(minSize, Math.min(1, height)),
            };

            this._renderCropBox();
        }

        _onPointerUp() {
            this.isDragging = false;
            window.removeEventListener('pointermove', this._boundOnPointerMove);
            window.removeEventListener('pointerup', this._boundOnPointerUp);
            window.removeEventListener('pointercancel', this._boundOnPointerUp);
        }

        /**
         * Render crop region onto offscreen canvas (with rotation) and POST to server
         */
        async processAndUpload() {
            if (!this.currentFile) return;

            try {
                if (this.processingScrim) {
                    this.processingScrim.classList.remove('d-none');
                }

                const img = new Image();
                img.crossOrigin = 'anonymous';
                await new Promise((resolve, reject) => {
                    img.onload = resolve;
                    img.onerror = reject;
                    img.src = this.originalUrl;
                });

                // Generate cropped & rotated canvas
                const natW = img.naturalWidth;
                const natH = img.naturalHeight;

                // First apply rotation to an intermediate canvas if needed
                let baseCanvas = document.createElement('canvas');
                if (this.rotation % 180 !== 0) {
                    baseCanvas.width = natH;
                    baseCanvas.height = natW;
                } else {
                    baseCanvas.width = natW;
                    baseCanvas.height = natH;
                }

                const baseCtx = baseCanvas.getContext('2d');
                baseCtx.translate(baseCanvas.width / 2, baseCanvas.height / 2);
                baseCtx.rotate((this.rotation * Math.PI) / 180);
                baseCtx.drawImage(img, -natW / 2, -natH / 2);

                // Now crop from baseCanvas
                const sx = Math.max(0, Math.floor(this.cropRect.x * baseCanvas.width));
                const sy = Math.max(0, Math.floor(this.cropRect.y * baseCanvas.height));
                const sw = Math.min(baseCanvas.width - sx, Math.ceil(this.cropRect.width * baseCanvas.width));
                const sh = Math.min(baseCanvas.height - sy, Math.ceil(this.cropRect.height * baseCanvas.height));

                const cropCanvas = document.createElement('canvas');
                cropCanvas.width = sw;
                cropCanvas.height = sh;
                const cropCtx = cropCanvas.getContext('2d');
                cropCtx.drawImage(baseCanvas, sx, sy, sw, sh, 0, 0, sw, sh);

                // Convert to blob
                const blob = await new Promise(resolve => cropCanvas.toBlob(resolve, 'image/jpeg', 0.95));
                if (!blob) throw new Error('Failed to generate cropped image.');

                // Upload - straight to the bucket through the shared engine.
                // The crop is a small blob, but the same direct path keeps
                // every upload in the app on one mechanism.
                if (!window.resumableUpload || typeof window.resumableUpload.start !== 'function') {
                    throw new Error('The upload engine is not ready yet. Please refresh and try again.');
                }

                const file = new File([blob], 'timetable_cropped.jpg', { type: 'image/jpeg' });
                const result = await window.resumableUpload.start(file, {
                    kind: 'timetable',
                    timetable: {
                        programme: this.options.programmeId || '',
                        academic_level: this.options.yearNum || '',
                        semester: this.options.semNum || '',
                        change_summary: this.options.changeSummary || '',
                    },
                });

                if (result && result.success !== false) {
                    this.close();
                    if (typeof this.options.onComplete === 'function') {
                        this.options.onComplete(result);
                    } else {
                        // Reload dashboard timetable panel or page
                        if (window.reloadTimetablePanel) {
                            window.reloadTimetablePanel();
                        } else {
                            window.location.reload();
                        }
                    }
                } else {
                    alert((result && result.detail) || 'Failed to process timetable. Please try again.');
                    if (this.processingScrim) {
                        this.processingScrim.classList.add('d-none');
                    }
                }
            } catch (err) {
                console.error('[TIMETABLE_CROPPER] Error processing upload:', err);
                alert('An error occurred while uploading your cropped timetable: ' + err.message);
                if (this.processingScrim) {
                    this.processingScrim.classList.add('d-none');
                }
            }
        }
    }

    // Expose singleton instance
    window.TimetableCropper = new TimetableCroppingEngine();

})(window, document);
