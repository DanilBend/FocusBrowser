/*******************************************************************************

    uBlock Origin - a comprehensive, efficient content blocker
    Copyright (C) 2014-present Raymond Hill

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see {http://www.gnu.org/licenses/}.

    Home: https://github.com/gorhill/uBlock
*/

import { dom, qs$ } from './dom.js';

/******************************************************************************/

const wikiLink = qs$('.wikilink');
const defaultWikiLink = wikiLink.href;

const updateWikiLink = paneWindow => {
    const candidate = paneWindow.wikilink;
    if ( typeof candidate !== 'string' || candidate === '' ) {
        wikiLink.href = defaultWikiLink;
        return;
    }
    try {
        const url = new URL(candidate);
        wikiLink.href = url.protocol === 'https:'
            ? url.href
            : defaultWikiLink;
    } catch {
        wikiLink.href = defaultWikiLink;
    }
};

/******************************************************************************/

function discardUnsavedData(synchronous = false) {
    const paneFrame = qs$('#iframe');
    const paneWindow = paneFrame.contentWindow;
    if (
        typeof paneWindow.hasUnsavedData !== 'function' ||
        paneWindow.hasUnsavedData() === false
    ) {
        return true;
    }

    if ( synchronous ) {
        return false;
    }

    return new Promise(resolve => {
        const modal = qs$('#unsavedWarning');
        const previouslyFocused = document.activeElement;
        const focusable = Array.from(
            modal.querySelectorAll('button:not([disabled])')
        );
        let done = false;
        dom.cl.add(modal, 'on');
        modal.setAttribute('aria-hidden', 'false');
        (focusable[0] || modal).focus();

        const onDone = status => {
            if ( done ) { return; }
            done = true;
            dom.cl.remove(modal, 'on');
            modal.setAttribute('aria-hidden', 'true');
            dom.off(document, 'click', onClick, true);
            dom.off(document, 'keydown', onKeydown, true);
            if (
                previouslyFocused instanceof HTMLElement &&
                previouslyFocused.isConnected
            ) {
                previouslyFocused.focus();
            }
            resolve(status);
        };

        const onClick = ev => {
            const target = ev.target;
            if ( target instanceof Element === false ) { return; }
            if ( target.closest('[data-i18n="dashboardUnsavedWarningStay"]') ) {
                return onDone(false);
            }
            if ( target.closest('[data-i18n="dashboardUnsavedWarningIgnore"]') ) {
                return onDone(true);
            }
            if ( qs$(modal, '[data-i18n="dashboardUnsavedWarning"]').contains(target) ) {
                return;
            }
            onDone(false);
        };

        const onKeydown = ev => {
            if ( ev.key === 'Escape' ) {
                ev.preventDefault();
                onDone(false);
                return;
            }
            if ( ev.key !== 'Tab' || focusable.length === 0 ) { return; }
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if ( ev.shiftKey && document.activeElement === first ) {
                ev.preventDefault();
                last.focus();
            } else if ( ev.shiftKey === false && document.activeElement === last ) {
                ev.preventDefault();
                first.focus();
            }
        };

        dom.on(document, 'click', onClick, true);
        dom.on(document, 'keydown', onKeydown, true);
    });
}

function loadDashboardPanel(pane, first) {
    const tabButton = qs$(`[data-pane="${pane}"]`);
    if ( tabButton === null || dom.cl.has(tabButton, 'selected') ) { return; }
    const loadPane = ( ) => {
        self.location.replace(`#${pane}`);
        dom.cl.remove('.tabButton.selected', 'selected');
        dom.attr('.tabButton[aria-current]', 'aria-current', null);
        dom.cl.add(tabButton, 'selected');
        dom.attr(tabButton, 'aria-current', 'page');
        tabButton.scrollIntoView();
        const iframe = qs$('#iframe');
        iframe.title = tabButton.textContent.trim() || iframe.title;
        iframe.contentWindow.location.replace(pane);
        if ( pane !== 'no-dashboard.html' ) {
            iframe.addEventListener('load', ( ) => {
                updateWikiLink(iframe.contentWindow);
            }, { once: true });
            vAPI.localStorage.setItem('dashboardLastVisitedPane', pane);
        }
    };
    if ( first ) {
        return loadPane();
    }
    const r = discardUnsavedData();
    if ( r === false ) { return; }
    if ( r === true ) { return loadPane(); }
    r.then(status => {
        if ( status === false ) { return; }
        loadPane();
    });
}

function onTabClickHandler(ev) {
    loadDashboardPanel(dom.attr(ev.target, 'data-pane'));
}

if ( self.location.hash.slice(1) === 'no-dashboard.html' ) {
    dom.cl.add(dom.body, 'noDashboard');
}

(async ( ) => {
    // Wait for uBO's main process to be ready
    await new Promise(resolve => {
        const check = async ( ) => {
            try {
                const response = await vAPI.messaging.send('dashboard', {
                    what: 'readyToFilter'
                });
                if ( response ) { return resolve(true); }
                const iframe = qs$('#iframe');
                if ( iframe.src !== '' ) {
                    iframe.src = '';
                }
            } catch {
            }
            vAPI.defer.once(250).then(( ) => check());
        };
        check();
    });

    dom.cl.remove(dom.body, 'notReady');

    const results = await Promise.all([
        // https://github.com/uBlockOrigin/uBlock-issues/issues/106
        vAPI.messaging.send('dashboard', { what: 'dashboardConfig' }),
        vAPI.localStorage.getItemAsync('dashboardLastVisitedPane'),
    ]);

    {
        const details = results[0] || {};
        if ( details.noDashboard ) {
            self.location.hash = '#no-dashboard.html';
            dom.cl.add(dom.body, 'noDashboard');
        } else if ( self.location.hash === '#no-dashboard.html' ) {
            self.location.hash = '';
        }
    }

    {
        let pane = results[1] || null;
        if ( self.location.hash !== '' ) {
            pane = self.location.hash.slice(1) || null;
        }
        loadDashboardPanel(pane !== null ? pane : 'settings.html', true);

        dom.on('.tabButton', 'click', onTabClickHandler);

        // https://developer.mozilla.org/en-US/docs/Web/API/Window/beforeunload_event
        dom.on(self, 'beforeunload', ( ) => {
            if ( discardUnsavedData(true) ) { return; }
            event.preventDefault();
            event.returnValue = '';
        });

        // https://developer.mozilla.org/en-US/docs/Web/API/Window/beforeunload_event
        dom.on(self, 'hashchange', ( ) => {
            const pane = self.location.hash.slice(1);
            if ( pane === '' ) { return; }
            loadDashboardPanel(pane);
        });

    }
})();
