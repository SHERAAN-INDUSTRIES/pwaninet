/** Image-only gestures; all listeners are removed when the viewer changes media. */
export function createMediaZoom(viewport, image, onChange) {
    let scale = 1, x = 0, y = 0, gesture = null, down = null, lastTap = null;
    const pointers = new Map();
    const listeners = new AbortController();
    const listen = (node, name, handler, options = {}) => node.addEventListener(name, handler, { ...options, signal: listeners.signal });
    const clamp = (value, limit) => Math.max(-limit, Math.min(limit, value));
    function draw() {
        x = clamp(x, Math.max(0, (image.clientWidth * scale - viewport.clientWidth) / 2));
        y = clamp(y, Math.max(0, (image.clientHeight * scale - viewport.clientHeight) / 2));
        image.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
        viewport.classList.toggle('zoomed', scale > 1);
        onChange(scale);
    }
    function zoom(value, clientX, clientY) {
        const rect = viewport.getBoundingClientRect();
        const px = (clientX ?? rect.left + rect.width / 2) - rect.left - rect.width / 2;
        const py = (clientY ?? rect.top + rect.height / 2) - rect.top - rect.height / 2;
        const next = Math.max(1, Math.min(5, value)), ratio = next / scale;
        x = px - (px - x) * ratio; y = py - (py - y) * ratio; scale = next;
        draw();
    }
    function measure() {
        const points = [...pointers.values()];
        if (points.length < 2) return null;
        const [a, b] = points;
        return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, distance: Math.hypot(a.x - b.x, a.y - b.y) };
    }
    listen(viewport, 'pointerdown', event => {
        if (event.button !== 0) return;
        pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
        viewport.setPointerCapture(event.pointerId);
        down = pointers.size === 1 ? { x: event.clientX, y: event.clientY, time: performance.now() } : null;
        gesture = measure();
    });
    listen(viewport, 'pointermove', event => {
        const previous = pointers.get(event.pointerId); if (!previous) return;
        event.preventDefault();
        pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
        if (down && Math.hypot(event.clientX - down.x, event.clientY - down.y) > 8) down = null;
        const next = measure();
        if (next && gesture?.distance > 0) {
            zoom(scale * next.distance / gesture.distance, gesture.x, gesture.y);
            x += next.x - gesture.x; y += next.y - gesture.y; draw();
        } else if (pointers.size === 1 && scale > 1) {
            x += event.clientX - previous.x; y += event.clientY - previous.y; draw();
        }
        gesture = next;
    });
    function end(event) {
        if (event.type === 'pointerup' && event.pointerType === 'touch' && down && performance.now() - down.time < 250) {
            const tap = { x: event.clientX, y: event.clientY, time: performance.now() };
            if (lastTap && tap.time - lastTap.time < 300 && Math.hypot(tap.x - lastTap.x, tap.y - lastTap.y) < 30) {
                zoom(scale > 1 ? 1 : 2.5, tap.x, tap.y); lastTap = null;
            } else lastTap = tap;
        }
        pointers.delete(event.pointerId); gesture = measure(); down = null;
    }
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(name => listen(viewport, name, end));
    listen(viewport, 'dblclick', event => {
        event.preventDefault();
        // Touch double-taps are already handled on pointerup.
        if (!event.sourceCapabilities?.firesTouchEvents) zoom(scale > 1 ? 1 : 2.5, event.clientX, event.clientY);
    });
    listen(viewport, 'wheel', event => {
        event.preventDefault(); zoom(scale * Math.exp(-event.deltaY * 0.002), event.clientX, event.clientY);
    }, { passive: false });
    listen(image, 'dragstart', event => event.preventDefault());
    listen(image, 'load', draw);
    const resize = new ResizeObserver(draw); resize.observe(viewport);
    draw();
    return { zoomBy: factor => zoom(scale * factor), reset: () => zoom(1), destroy() { listeners.abort(); resize.disconnect(); pointers.clear(); } };
}
