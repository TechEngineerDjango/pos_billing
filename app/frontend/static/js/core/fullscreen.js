/**
 * Fullscreen mode is exited by the browser on every full-page navigation —
 * POS and Dashboard are separate server-rendered pages (real <a href> links,
 * not SPA routes), and the Fullscreen API spec forces exit on any top-level
 * navigation. Best-effort recovery: remember it was on right before the page
 * unloads, then try to re-enter it once the next page loads.
 *
 * Not guaranteed everywhere — requestFullscreen() needs a fresh user
 * gesture, and some browsers (notably Safari/Firefox) don't treat "a page
 * just loaded after a gesture-initiated navigation" as one, so this can
 * silently fail there even though it works in Chrome/Edge.
 */
(function () {
    const FLAG = 'wasFullscreen';

    window.addEventListener('beforeunload', () => {
        if (document.fullscreenElement) {
            sessionStorage.setItem(FLAG, '1');
        }
    });

    if (sessionStorage.getItem(FLAG)) {
        sessionStorage.removeItem(FLAG);
        document.documentElement.requestFullscreen().catch(() => {});
    }
})();
