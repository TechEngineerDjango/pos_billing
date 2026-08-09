/**
 * In-page navigation between POS (/billing/) and Dashboard (/admin/) —
 * fetches the destination page and swaps #page-main/#page-scripts instead of
 * a real browser navigation. The Fullscreen API force-exits fullscreen on
 * any top-level document navigation (by spec, in every browser), so this is
 * the only way fullscreen survives switching between the two.
 *
 * Scoped deliberately narrow: only these two exact routes are intercepted.
 * Every other link (logout, change password, superadmin, external) falls
 * through to a normal navigation untouched.
 */
(function () {
    const SPA_ROUTES = ['/billing/', '/admin/'];

    // Tracks which page scripts (by absolute URL) have already run once in
    // this SPA session. A second run isn't just wasteful — dashboard-app.js
    // declares top-level `let`/`const` (revenueChart, hourlyChart,
    // chartJsPromise), and those bindings live in the page's single shared
    // global lexical scope across every classic <script>, unlike `function`
    // declarations which just get reassigned onto window. Re-inserting the
    // same script a second time throws "Identifier ... has already been
    // declared" and aborts navigation. A DOM query for "is a script with
    // this src already present" doesn't work here, deliberately: each swap
    // fully replaces #page-main/#page-scripts, so the only match it would
    // ever find is the very node it's currently looking at.
    const loadedScripts = new Set();

    // Seed with whatever #page-main/#page-scripts already loaded natively on
    // this cold page load — without this, the first SPA trip back to this
    // same page re-declares against those original bindings too.
    ['page-main', 'page-scripts'].forEach((id) => {
        const region = document.getElementById(id);
        if (!region) return;
        region.querySelectorAll('script[src]').forEach((s) => {
            loadedScripts.add(new URL(s.getAttribute('src'), window.location.href).href);
        });
    });

    function isSpaNavigable(href) {
        let u;
        try {
            u = new URL(href, window.location.origin);
        } catch (e) {
            return false;
        }
        return u.origin === window.location.origin && SPA_ROUTES.includes(u.pathname);
    }

    // Executes the real <script src>/inline tags found in a parsed (not yet
    // inserted into the live document) region, appending each to <head> and
    // awaiting external ones before returning. This must finish BEFORE the
    // fetched HTML is written into #page-main — Alpine already has its own
    // MutationObserver running once started, and it reacts to that innerHTML
    // write immediately; if x-data="posApp()" lands in the live DOM before
    // pos-app.js has actually defined posApp(), Alpine's own observer tries
    // (and fails) to bind it right then, before our own later Alpine.initTree
    // call ever gets a chance. JSON data-island scripts
    // (type="application/json") aren't real scripts and are skipped — they
    // just need to exist in the DOM, which the innerHTML write handles.
    function runScripts(regionNode) {
        const pending = [];
        Array.from(regionNode.querySelectorAll('script')).forEach((old) => {
            if (old.type === 'application/json') return;
            const src = old.getAttribute('src');
            if (src) {
                const abs = new URL(src, window.location.href).href;
                if (loadedScripts.has(abs)) return;
                loadedScripts.add(abs);
            }
            const fresh = document.createElement('script');
            for (const attr of old.attributes) fresh.setAttribute(attr.name, attr.value);
            fresh.textContent = old.textContent;
            if (src) {
                // A dynamically created <script src> loads asynchronously by
                // default (unlike a parser-inserted one) — await its load so
                // callers can rely on it having executed once this resolves.
                fresh.async = false;
                pending.push(new Promise((resolve) => {
                    fresh.onload = resolve;
                    fresh.onerror = resolve; // don't hang navigation on a failed script
                }));
            }
            document.head.appendChild(fresh);
        });
        return Promise.all(pending);
    }

    async function navigateTo(url, push) {
        const main = document.getElementById('page-main');
        const scriptsHost = document.getElementById('page-scripts');
        if (!main || !scriptsHost) { window.location.href = url; return; }

        let html;
        try {
            const res = await fetch(url, { headers: { 'X-Requested-With': 'fetch' } });
            if (!res.ok) throw new Error('nav fetch failed: ' + res.status);
            html = await res.text();
        } catch (e) {
            window.location.href = url;
            return;
        }

        const doc = new DOMParser().parseFromString(html, 'text/html');
        const newMain = doc.getElementById('page-main');
        const newScriptsHost = doc.getElementById('page-scripts');
        if (!newMain || !newScriptsHost) { window.location.href = url; return; }

        // Load the destination page's scripts (still off-DOM at this point,
        // parsed by DOMParser — Alpine's observer can't see them yet) before
        // touching the live document at all.
        await runScripts(newMain);
        await runScripts(newScriptsHost);

        if (window.Alpine && typeof Alpine.destroyTree === 'function') {
            Alpine.destroyTree(main);
        }

        document.title = doc.title;
        main.innerHTML = newMain.innerHTML;
        scriptsHost.innerHTML = newScriptsHost.innerHTML;

        if (push) history.pushState({ spaNav: true }, '', url);

        if (window.Alpine && typeof Alpine.initTree === 'function') {
            Alpine.initTree(main);
        }

        window.scrollTo(0, 0);
    }

    document.addEventListener('click', (e) => {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        const a = e.target.closest('a');
        if (!a || !a.href) return;
        if (a.target && a.target !== '_self') return;
        if (a.hasAttribute('download')) return;
        if (!isSpaNavigable(a.href)) return;
        if (new URL(a.href, window.location.origin).pathname === window.location.pathname) return;
        e.preventDefault();
        navigateTo(a.href, true);
    });

    window.addEventListener('popstate', () => {
        navigateTo(window.location.href, false);
    });
})();
