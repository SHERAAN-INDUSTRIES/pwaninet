/**
 * Single morphing status modal — success → spinner → dismiss.
 */
(function (global) {
    'use strict';

    const MODAL_ID = 'vastoStatusModal';

    function delay(ms) {
        return new Promise(function (resolve) {
            setTimeout(resolve, ms);
        });
    }

    function getElements() {
        return {
            modalEl: document.getElementById(MODAL_ID),
            iconEl: document.getElementById('vastoStatusModalIcon'),
            titleEl: document.getElementById('vastoStatusModalTitle'),
            subtitleEl: document.getElementById('vastoStatusModalSubtitle'),
        };
    }

    function renderIcon(iconEl, icon) {
        if (!iconEl) return;
        if (icon === 'spinner') {
            iconEl.innerHTML =
                '<div class="spinner-border text-primary" style="width: 3rem; height: 3rem;" role="status">' +
                '<span class="visually-hidden">Loading...</span></div>';
        } else {
            iconEl.innerHTML =
                '<i aria-hidden="true" data-lucide="circle-check-big" class="vasto-icon text-success" style="--vasto-icon-size: 3rem;"></i>';
        }
    }

    function applyStep(step) {
        const { iconEl, titleEl, subtitleEl } = getElements();
        renderIcon(iconEl, step.icon || 'check');
        if (titleEl) titleEl.textContent = step.title || '';
        if (subtitleEl) {
            subtitleEl.textContent = step.subtitle || '';
            subtitleEl.style.display = step.subtitle ? 'block' : 'none';
        }
    }

    function getModalInstance() {
        const { modalEl } = getElements();
        if (!modalEl || typeof bootstrap === 'undefined') return null;
        return bootstrap.Modal.getOrCreateInstance(modalEl, {
            backdrop: true,
            keyboard: false,
        });
    }

    function cleanupBackdrops() {
        document.querySelectorAll('.modal-backdrop').forEach(function (el) {
            el.remove();
        });
        document.body.classList.remove('modal-open');
        document.body.style.removeProperty('overflow');
        document.body.style.removeProperty('padding-right');
    }

    function hideModalAndThen(callback) {
        const { modalEl } = getElements();
        const modal = getModalInstance();
        if (!modal || !modalEl) {
            cleanupBackdrops();
            if (callback) callback();
            return;
        }

        function onHidden() {
            modalEl.removeEventListener('hidden.bs.modal', onHidden);
            cleanupBackdrops();
            if (callback) callback();
        }

        modalEl.addEventListener('hidden.bs.modal', onHidden);
        modal.hide();
    }

    async function runSteps(steps, options) {
        options = options || {};
        const modal = getModalInstance();
        if (!modal || !steps || !steps.length) {
            cleanupBackdrops();
            if (options.onComplete) options.onComplete();
            return;
        }

        modal.show();

        for (let i = 0; i < steps.length; i++) {
            applyStep(steps[i]);
            const duration = steps[i].durationMs;
            if (duration && duration > 0) {
                await delay(duration);
            }
        }

        hideModalAndThen(options.onComplete);
    }

    global.vastoStatusModal = {
        runSteps: runSteps,
        hide: function () {
            hideModalAndThen(null);
        },
        cleanupBackdrops: cleanupBackdrops,
    };
})(window);
