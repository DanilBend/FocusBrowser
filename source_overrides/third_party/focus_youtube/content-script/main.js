// Copyright 2026 The Focus Browser Authors

'use strict';

if (globalThis.__focusYoutubeLoaded !== true) {
globalThis.__focusYoutubeLoaded = true;

const browser = globalThis.chrome;
const SETTINGS = globalThis.FocusYoutubeSettings;
const HTML = document.documentElement;
const IS_TOP_FRAME = window === window.top;

const REDIRECT_PATHS = {
  redirect_to_subs: '/feed/subscriptions',
  redirect_to_wl: '/playlist/?list=WL',
  redirect_to_library: '/feed/you',
};

function redirectUrl(settingId) {
  return new URL(REDIRECT_PATHS[settingId], location.origin).href;
}

const cache = {};
const closedRevealBoxes = new Set();
const temporarilyRevealed = new Set();
const REVEAL_BOX_CONFIGS = [
  {
    containerSelector: 'ytd-page-manager',
    boxId: 'focus_youtube_homepage_reveal',
    removeSetting: 'remove_homepage',
    revealSetting: 'add_reveal_homepage',
  },
  {
    containerSelector: '#secondary-inner',
    boxId: 'focus_youtube_sidebar_reveal',
    removeSetting: 'remove_sidebar',
    revealSetting: 'add_reveal_sidebar',
  },
  {
    containerSelector: '#movie_player',
    boxId: 'focus_youtube_endscreen_reveal',
    removeSetting: 'remove_end_of_video',
    revealSetting: 'add_reveal_end_of_video',
  },
];

let url = location.href;
let onResultsPage = false;
let onHomepage = false;
let onShorts = false;
let onVideo = false;
let onChannel = false;
let onSubs = false;
let dynamicIters = 0;
let frameRequested = false;
let isRunning = false;
let lastRedirect;
let hyper = false;
let savedPlayback = null;
let fallbackTimer = null;
let domObserver = null;
const redirectInterval = 1_000;

const DYNAMIC_SETTING_IDS = new Set([
  'remove_all_shorts', 'remove_shorts_results', 'remove_playables',
  'remove_all_but_one',
  'disable_channel_autoplay', 'remove_channel_for_you', 'remove_sub_shorts',
  'remove_sub_live', 'remove_sub_upcoming', 'remove_sub_premiere',
  'remove_sub_vods', 'remove_sub_most_relevant', 'disable_autoplay',
  'enable_theater', 'auto_skip_ads', 'remove_non_timestamp_comments',
  'expand_description', 'remove_playlist_suggestions', 'remove_notif_bell',
  'search_engine_mode', 'remove_video_thumbnails',
  'remove_extra_sidebar_tags', 'only_show_playlists', 'remove_clip_button',
  'redirect_to_subs', 'redirect_to_wl', 'redirect_to_library',
  'normalize_shorts', 'autofocus_search', 'add_reveal_homepage',
  'add_reveal_sidebar', 'add_reveal_end_of_video',
]);

function setBehaviorAttribute(id, value) {
  if (!SETTINGS.behaviorIdSet.has(id) && id !== 'global_enable') return;
  HTML.setAttribute(id, value === true ? 'true' : 'false');
}

function applySettings(settings) {
  SETTINGS.behaviorIds.forEach(id => {
    cache[id] = settings[id] === true;
    setBehaviorAttribute(id, cache[id]);
  });
  cache.global_enable = settings.global_enable;
  setBehaviorAttribute('global_enable', settings.global_enable);
}

function persistChanges(changes) {
  const automation = {};
  const regular = {};
  for (const [id, value] of Object.entries(changes)) {
    (SETTINGS.automationIds.includes(id) ? automation : regular)[id] = value;
  }
  if (Object.keys(regular).length) {
    browser.storage.local.set(regular);
  }
  if (!Object.keys(automation).length) return;
  browser.runtime.sendMessage({
    type: 'focusyoutube.updateAutomation',
    changes: automation,
  }, response => {
    const error = browser.runtime.lastError;
    if (error || response?.ok !== true) {
      console.warn('FocusYoutube automation update failed');
    }
  });
}

function persistMigration(changes) {
  const regular = Object.fromEntries(Object.entries(changes)
      .filter(([id]) => !SETTINGS.automationIds.includes(id)));
  if (Object.keys(regular).length) {
    browser.storage.local.set(regular);
  }
  if (!Object.keys(changes).some(id => SETTINGS.automationIds.includes(id))) {
    return;
  }
  browser.runtime.sendMessage({
    type: 'focusyoutube.reconcileAutomation',
  }, response => {
    const error = browser.runtime.lastError;
    if (error || response?.ok !== true) {
      console.warn('FocusYoutube automation reconciliation failed');
    }
  });
}

function logStorageChange(changes, areaName) {
  if (areaName !== 'local') return;
  for (const [id, { oldValue, newValue }] of Object.entries(changes)) {
    if (oldValue === newValue) continue;
    if (SETTINGS.behaviorIdSet.has(id) || id === 'global_enable') {
      cache[id] = newValue === true;
      setBehaviorAttribute(id, cache[id]);
      if (newValue === true) temporarilyRevealed.delete(id);
    } else {
      continue;
    }
  }
  cleanupGeneratedMarks(cache.global_enable !== true);
  updateObserverState();
  requestRunDynamicSettings(0);
}
browser.storage.onChanged.addListener(logStorageChange);

browser.storage.local.get(saved => {
  const migration = SETTINGS.createMigration(saved);
  applySettings(migration.settings);
  if (Object.keys(migration.patch).length) {
    persistMigration(migration.patch);
  }
  if (migration.removeKeys.length) {
    browser.storage.local.remove(migration.removeKeys);
  }
  handleNewPage();
  cleanupGeneratedMarks(false);
  updateObserverState();
  requestRunDynamicSettings(0);
});

document.addEventListener('DOMContentLoaded', handleNewPage, { once: true });

// Dynamic settings (i.e. js instead of css)
function cleanupGeneratedMarks(force) {
  const removeWhenOff = (id, selector, attribute) => {
    if (!force && cache[id] === true) return;
    focusQsa(selector).forEach(node => node.removeAttribute(attribute));
  };
  const shortsOn = !force &&
      (cache.remove_all_shorts === true || cache.remove_shorts_results === true);
  if (!shortsOn) {
    focusQsa('[focus-short]').forEach(node => node.removeAttribute('focus-short'));
    focusQsa('[focus-marked-short]').forEach(node =>
      node.removeAttribute('focus-marked-short'));
  }
  removeWhenOff('remove_playables', '[focus-playable]', 'focus-playable');
  removeWhenOff('remove_all_but_one', '[focus-hidden-row]', 'focus-hidden-row');
  removeWhenOff('remove_channel_for_you', '[focus-channel-for-you]', 'focus-channel-for-you');
  removeWhenOff('remove_extra_sidebar_tags', '[focus-hide-chip]', 'focus-hide-chip');
  removeWhenOff('remove_clip_button', '[focus-scissor]', 'focus-scissor');
  removeWhenOff('remove_playlist_suggestions', '[focus-playlist-suggestions]', 'focus-playlist-suggestions');
  if (force ||
      (cache.search_engine_mode !== true &&
       cache.remove_video_thumbnails !== true)) {
    focusQsa('[data-focus-youtube-time]').forEach(node => node.remove());
  }

  const subscriptionsOn = !force && [
    'remove_sub_shorts', 'remove_sub_live', 'remove_sub_upcoming',
    'remove_sub_premiere', 'remove_sub_vods', 'remove_sub_most_relevant',
  ].some(id => cache[id] === true);
  if (!subscriptionsOn) {
    for (const attribute of [
      'focus-sub-short', 'focus-badge', 'focus-vod',
      'focus-most-relevant', 'focus-empty',
    ]) {
      focusQsa('[' + attribute + ']').forEach(node =>
        node.removeAttribute(attribute));
    }
    focusQsa('[data-focus-youtube-row]').forEach(node => {
      node.style.removeProperty('--ytd-rich-grid-items-per-row');
      node.removeAttribute('data-focus-youtube-row');
    });
  }
}

function restoreAdPlayback() {
  if (!hyper) return;
  const video = focusQs('video');
  if (video) {
    video.playbackRate = savedPlayback?.playbackRate ?? 1;
    video.muted = savedPlayback?.muted ?? false;
  }
  hyper = false;
  savedPlayback = null;
}

function normalizedText(value) {
  return String(value || '').trim().toLocaleLowerCase();
}

function classifyBroadcastBadge(value) {
  const text = normalizedText(value);
  if (/\bupcoming\b|запланирован|ожидается/.test(text)) return 'upcoming';
  if (/\bpremiere\b|премьер/.test(text)) return 'premiere';
  if (/\blive\b|прямой эфир|в эфире/.test(text)) return 'live';
  return '';
}

function runDynamicSettings() {
  if (isRunning) return;
  isRunning = true;
  dynamicIters += 1;
  const on = cache.global_enable === true;
  cleanupGeneratedMarks(!on);
  if (!on) {
    restoreAdPlayback();
    frameRequested = false;
    isRunning = false;
    updateObserverState();
    scheduleFallback();
    return;
  }
  if (cache.auto_skip_ads !== true) restoreAdPlayback();

  // Check if the URL has changed (YouTube is a Single-Page Application)
  if (url !== location.href) {
    handleNewPage();
  }

  // Double check for redirects. Also reset dynamicIters so the
  // reveal box creation code keeps running on the homepage.
  if (IS_TOP_FRAME && onHomepage && !cache.redirect_off) {
    dynamicIters = 0;
    if (IS_TOP_FRAME) checkRedirects();
  }

  // Dynamic settings
  try {

    // Pause autoplaying channel trailers
    if (cache['disable_channel_autoplay'] && dynamicIters <= 10) {
      qs('ytd-channel-video-player-renderer video')?.pause();
    }

    // Hide all shorts
    if (cache['remove_all_shorts']) {
      const shortsBadgeSelector = 'ytd-thumbnail-overlay-time-status-renderer[overlay-style="SHORTS"]';
      const shortBadges = qsa(shortsBadgeSelector);
      shortBadges?.forEach(badge => {
        const sidebarVid = badge.closest('ytd-compact-video-renderer');
        sidebarVid?.setAttribute('focus-short', '');
        const gridVideo = badge.closest('ytd-grid-video-renderer');
        gridVideo?.setAttribute('focus-short', '');
        const updatedGridVideo = badge.closest('ytd-rich-item-renderer');
        updatedGridVideo?.setAttribute('focus-short', '');
      });

      const shortsShelfSelector = '*[is-shorts]';
      const shortsShelves = qsa(shortsShelfSelector);
      shortsShelves?.forEach(shelf => {
        const shelfContainer = shelf.closest('ytd-rich-section-renderer');
        shelfContainer?.setAttribute('focus-short', '');
      });
    }

    // Hide playables
    if (cache['remove_playables']) {
      const playableCards = qsa('ytd-mini-game-card-view-model');
      playableCards?.forEach(card => {
        const shelfContainer = card.closest('ytd-rich-section-renderer');
        shelfContainer?.setAttribute('focus-playable', '');
      });
    }

    // Hide all but the first row of homepage suggestions
    if (onHomepage) {
      const grid = qs('ytd-browse[page-subtype="home"] ytd-rich-grid-renderer');
      if (grid) {
        const items = qsa(':scope > #contents > ytd-rich-item-renderer', grid);
        if (cache['remove_all_but_one']) {
          const perRow = parseInt(getComputedStyle(grid).getPropertyValue('--ytd-rich-grid-items-per-row')) || 4;
          items.forEach((item, i) => {
            if (i >= perRow) item.setAttribute('focus-hidden-row', '');
            else item.removeAttribute('focus-hidden-row');
          });
        } else {
          items.forEach(item => item.removeAttribute('focus-hidden-row'));
        }
      }
    }

    // Channel page option
    if (onChannel) {
      if (cache['remove_channel_for_you']) {
        const forYouSection = qsa('ytd-item-section-renderer[page-subtype=channels]').find(node => {
          const title = qs('span#title', node);
          return ['for you', 'для вас'].includes(normalizedText(title?.innerText));
        });
        forYouSection?.setAttribute('focus-channel-for-you', '');
      }
    }

    // Subscriptions page options (only if any sub-page settings are enabled)
    const subsSettingsEnabled = cache['remove_sub_shorts'] || cache['remove_sub_live'] ||
                                cache['remove_sub_upcoming'] || cache['remove_sub_premiere'] || cache['remove_sub_vods'] ||
                                cache['remove_sub_most_relevant'];
    if (onSubs && subsSettingsEnabled) {
      const badgeSelector = 'ytd-badge-supported-renderer';
      const upcomingBadgeSelector = 'ytd-thumbnail-overlay-time-status-renderer[overlay-style="UPCOMING"]';
      const shortsBadgeSelector = 'ytd-thumbnail-overlay-time-status-renderer[overlay-style="SHORTS"]';
      const addBadgeTextToVideo = badge => {
        const badgeText = classifyBroadcastBadge(badge.innerText);
        if (badgeText) {
          const gridVideo = badge.closest('ytd-grid-video-renderer');
          const updatedGridVideo = badge.closest('ytd-rich-item-renderer');
          gridVideo?.setAttribute('focus-badge', badgeText);
          updatedGridVideo?.setAttribute('focus-badge', badgeText);
        }
      };

      // Live / Premiere
      const badges = qsa(badgeSelector);
      badges.forEach(addBadgeTextToVideo);

      // Upcoming
      const upcomingBadges = qsa(upcomingBadgeSelector);
      upcomingBadges.forEach(addBadgeTextToVideo);

      // Shorts
      const shortBadges = qsa(shortsBadgeSelector);
      shortBadges.forEach(badge => {
        const video = badge.closest('ytd-grid-video-renderer');
        const updatedGridVideo = badge.closest('ytd-rich-item-renderer');
        video?.setAttribute('focus-sub-short', '');
        updatedGridVideo?.setAttribute('focus-sub-short', '');
      });

      // VODs
      const vodSelector = '#metadata-line span';
      const vodSpans = qsa(vodSelector).filter(span =>
        /streamed|трансляц|эфир заверш/i.test(span.innerText));
      vodSpans.forEach(span => {
        const video = span.closest('ytd-grid-video-renderer');
        const updatedGridVideo = span.closest('ytd-rich-item-renderer');
        video?.setAttribute('focus-vod', '');
        updatedGridVideo?.setAttribute('focus-vod', '');
      });

      // "Most relevant" shelf
      if (cache['remove_sub_most_relevant']) {
        const shelves = qsa('ytd-rich-section-renderer ytd-rich-shelf-renderer');
        shelves.forEach(shelf => {
          const title = qs('span#title', shelf);
          if (['most relevant', 'самое актуальное'].includes(
              normalizedText(title?.innerText))) {
            const section = shelf.closest('ytd-rich-section-renderer');
            section?.setAttribute('focus-most-relevant', '');
          }
        });
      }

      // Reduce empty space.
      const subsRows = qsa('ytd-rich-grid-row');
      subsRows.forEach(row => {
        const contents = qs('#contents', row);
        if (!contents) return;
        const items = qsa('ytd-rich-item-renderer', contents);
        if (!items) return;
        const activeItems = items.filter(item => item.offsetParent);
        activeItems.forEach(item => item.style.setProperty('--ytd-rich-grid-items-per-row', activeItems.length));
        row.setAttribute('focus-empty', String(activeItems.length === 0));
        row.setAttribute('data-focus-youtube-row', 'true');
      });
    }

    if (!onSubs || !subsSettingsEnabled) cleanupGeneratedMarks(false);

    // Hide shorts on the results page
    if (onResultsPage &&
        (cache.remove_shorts_results || cache.remove_all_shorts)) {
      const shortResults = qsa('a[href^="/shorts/"]:not([focus-marked-short])');
      shortResults.forEach(sr => {
        sr.setAttribute('focus-marked-short', 'true');
        const result = sr.closest('ytd-video-renderer');
        result?.setAttribute('focus-short', true);

        // Mobile
        const mobileResult = sr.closest('ytm-video-with-context-renderer');
        mobileResult?.setAttribute('focus-short', true);
      });
    }

    // Disable autoplay
    if (cache['disable_autoplay'] === true) {
      const autoplayButton = qsa('.ytp-autonav-toggle-button[aria-checked=true]');
      autoplayButton?.forEach(e => {
        if (e && e.offsetParent) {
          e.click();
        }
      });

      // mobile
      const mAutoplayButton = qsa('.ytm-autonav-toggle-button-container[aria-pressed=true]');
      mAutoplayButton?.forEach(e => {
        if (e && e.offsetParent) {
          e.click();
        }
      });
    }

    // Ambient mode and annotations are handled non-invasively by CSS.

    // Enable theater mode
    if (cache['enable_theater']) {
      const flexy = document.querySelector("ytd-watch-flexy");
      const btn = document.querySelector(".ytp-size-button");
      if (flexy && btn && !flexy.hasAttribute("theater")) {
        btn.click();
      }
    }

    // Skip through ads
    if (cache['auto_skip_ads'] === true) {

      // Close overlay ads.
      qsa('.ytp-ad-overlay-close-button')?.forEach(e => {
        if (e && e.offsetParent) {
          e.click();
        }
      });

      // Click on "Skip ad" button
      const skipButtons = qsa('.ytp-ad-skip-button, .ytp-ad-skip-button-modern, .ytp-skip-ad-button, .ytp-skip-ad button');

      const skippableAd = skipButtons?.some(button => button.offsetParent);
      if (skippableAd) {
        skipButtons?.forEach(e => {
          if (e && e.offsetParent) {
            e.click();
          }
        });
      } else {

        // Speed through ads that can't be skipped (yet).
        let adSelectors = [
          '.ytp-ad-player-overlay-instream-info',
          '.ytp-ad-button-icon'
        ];
        let adElements = adSelectors.flatMap(selector => qsa(selector));
        const adActive = adElements.some(elt => elt && window.getComputedStyle(elt).display !== 'none');
        const video = qs('video');
        if (adActive && video) {
          if (!hyper) {
            hyper = true;
            savedPlayback = {
              playbackRate: video.playbackRate,
              muted: video.muted,
            };
          }
          video.playbackRate = 10;
          video.muted = true;
        } else {
          if (hyper) {
            restoreAdPlayback();
          }
        }
      }
    }

    // Hide all but the timestamped comments
    if (cache['remove_non_timestamp_comments']) {
      const timestamps = qsa('yt-formatted-string:not(.published-time-text).ytd-comment-renderer > a.yt-simple-endpoint[href^="/watch"]');
      timestamps.forEach(timestamp => {
        const comment = timestamp.closest('ytd-comment-thread-renderer');
        comment?.setAttribute('timestamp_comment', '');
      });
    }

    // Show description
    if (cache.expand_description) {
      const expandButton = qsa('#description #expand.button');
      expandButton.forEach(b => {
        if (b && b.offsetParent) {
          b.click();
        }
      });
    }

    // Hide playlist suggestions
    if (cache['remove_playlist_suggestions']) {
      const identifier = qs('ytd-item-section-header-renderer[title-style="ITEM_SECTION_HEADER_TITLE_STYLE_PLAYLIST_RECOMMENDATIONS"]');
      if (identifier) {
        const section = identifier.closest('ytd-item-section-renderer');
        section?.setAttribute('focus-playlist-suggestions', '');
      }
    }

    // Hide notification number in title
    if (cache['remove_notif_bell']) {
      document.title = document.title.replace(/^\(\d+\)/g, '');
    }

    // Show video length when thumbnails are hidden
    if (cache['search_engine_mode'] || cache['remove_video_thumbnails']) {
      const thumbnails = qsa('ytd-thumbnail');
      thumbnails.forEach(thumbnail => {
        const videoRow = thumbnail.closest('ytd-video-renderer');
        if (!videoRow) return;
        const exists = qs('.inline-metadata-item[data-focus-youtube-time]', videoRow);
        if (exists) return;

        const timeNode = qs('ytd-thumbnail-overlay-time-status-renderer #text', thumbnail);
        const time = timeNode?.innerText?.trim();
        if (!time) return;

        const metadata = qs('#metadata-line', videoRow);
        if (!metadata) return;
        const lastMetadataLine = qs('.inline-metadata-item:last-of-type', metadata);
        if (!lastMetadataLine) return;

        // length metadata goes between views and age.
        const metadataLine = lastMetadataLine.cloneNode(true);
        metadataLine.setAttribute('data-focus-youtube-time', '');
        metadataLine.innerText = time;
        metadata.insertBefore(metadataLine, lastMetadataLine);
      });
    }

    // Hide all but the related tag in the sidebar
    if (onVideo && cache['remove_extra_sidebar_tags']) {
      const getChip = names => qsa(
          'yt-chip-cloud-chip-renderer yt-formatted-string').find(text =>
        names.includes(normalizedText(text.getAttribute('title') || text.innerText)))
          ?.closest('yt-chip-cloud-chip-renderer');
      const allChip = getChip(['all', 'все']);
      const relatedChip = getChip(['related', 'похожие']);
      const allowedChips = new Set([allChip, relatedChip].filter(Boolean));

      const chips = qsa('yt-chip-cloud-chip-renderer');
      chips.forEach(chip => {
        const hideChip = allowedChips.size > 0 && !allowedChips.has(chip);
        chip.toggleAttribute('focus-hide-chip', hideChip);
      });
    }

    // Reveal boxes exist only when both related switches are enabled. Keep
    // checking for their SPA containers: YouTube can mount them long after
    // the initial navigation, and settings can change without a reload.
    REVEAL_BOX_CONFIGS.forEach(({
        containerSelector, boxId, removeSetting, revealSetting,
      }) => {
        const existing = focusQs('#' + boxId);
        const shouldShow = cache[revealSetting] === true &&
            cache[removeSetting] === true &&
            !temporarilyRevealed.has(removeSetting) &&
            !closedRevealBoxes.has(boxId);
        if (!shouldShow) {
          existing?.remove();
          return;
        }
        if (existing) return;
        const container = focusQs(containerSelector);
        if (!container) return;

        const box = document.createElement('section');
        box.id = boxId;
        box.className = 'focus-youtube-reveal';
        box.setAttribute('aria-label', 'Скрытый раздел YouTube');
        const actions = document.createElement('div');
        actions.className = 'focus-youtube-reveal-actions';
        const dismiss = document.createElement('button');
        dismiss.type = 'button';
        dismiss.textContent = 'Больше не показывать';
        const close = document.createElement('button');
        close.type = 'button';
        close.setAttribute('aria-label', 'Закрыть');
        close.textContent = '×';
        const reveal = document.createElement('button');
        reveal.type = 'button';
        reveal.textContent = 'Показать временно';

        reveal.addEventListener('click', () => {
          temporarilyRevealed.add(removeSetting);
          HTML.setAttribute(removeSetting, 'false');
          box.remove();
        });
        dismiss.addEventListener('click', () => {
          updateSetting(revealSetting, false);
          box.remove();
        });
        close.addEventListener('click', () => {
          closedRevealBoxes.add(boxId);
          box.remove();
        });
        actions.append(dismiss, close);
        box.append(actions, reveal);
        container.appendChild(box);
      });

    // Expand the "You" section in the left sidebar
    if (cache['only_show_playlists']) {
      const showMoreButton = qs('#section-items > ytd-guide-collapsible-entry-renderer yt-interaction');
      if (showMoreButton && showMoreButton.offsetParent) {
        showMoreButton.click();
      }
    }

    // Video Player: hide the 'clip' button.
    //   The path[d=...] selector selects scissor SVGs.
    if (cache['remove_clip_button']) {
      qsa('path[d^="M8 7c0 .55-.45 1-1 1s-1-.45-1-1"]').
        map(path => path.closest('#menu button')).
        forEach(b => b?.setAttribute('focus-scissor', ''));
    }

  } catch (error) {
    console.log(error);
  }

  frameRequested = false;
  isRunning = false;
  updateObserverState();
  scheduleFallback();
}


function hasDynamicWork() {
  return cache.global_enable === true &&
      [...DYNAMIC_SETTING_IDS].some(id => cache[id] === true);
}

function requestRunDynamicSettings(delay = 120) {
  if (frameRequested || isRunning || document.hidden) return;
  frameRequested = true;
  setTimeout(() => {
    frameRequested = false;
    runDynamicSettings();
  }, delay);
}

function scheduleFallback() {
  if (fallbackTimer !== null) {
    clearTimeout(fallbackTimer);
    fallbackTimer = null;
  }
  if (!hasDynamicWork()) return;
  fallbackTimer = setTimeout(() => {
    fallbackTimer = null;
    requestRunDynamicSettings(0);
  }, hasDynamicWork() ? 1500 : 2000);
}

function updateObserverState() {
  domObserver?.disconnect();
  domObserver = null;
  if (!hasDynamicWork() || !document.documentElement) return;
  domObserver = new MutationObserver(() => requestRunDynamicSettings(180));
  domObserver.observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    domObserver?.disconnect();
    return;
  }
  updateObserverState();
  requestRunDynamicSettings(0);
});
for (const eventName of [
  'yt-navigate-finish',
  'yt-page-data-updated',
  'state-navigateend',
]) {
  window.addEventListener(eventName, () => {
    handleNewPage();
    requestRunDynamicSettings(0);
  });
}
window.addEventListener('popstate', () => {
  handleNewPage();
  requestRunDynamicSettings(0);
});

function checkRedirects() {
  if (!IS_TOP_FRAME) return;
  const on = cache['global_enable'] === true;
  if (
    on &&
    onHomepage &&
    !cache['redirect_off'] &&
    (!lastRedirect || Date.now() - lastRedirect > redirectInterval)
  ) {
    if (cache['redirect_to_subs']) {
      location.replace(redirectUrl('redirect_to_subs'));
      lastRedirect = Date.now();
    }
    if (cache['redirect_to_wl']) {
      location.replace(redirectUrl('redirect_to_wl'));
      lastRedirect = Date.now();
    }
    if (cache['redirect_to_library']) {
      location.replace(redirectUrl('redirect_to_library'));
      lastRedirect = Date.now();
    }
  }
}

function handleNewPage() {
  const on = cache['global_enable'] === true;
  dynamicIters = 0;
  const nextUrl = location.href;
  const didNavigate = nextUrl !== url;
  url = nextUrl;
  restoreAdPlayback();
  if (didNavigate) {
    temporarilyRevealed.clear();
    closedRevealBoxes.clear();
  }
  const page = focusYoutubePage(url);
  onResultsPage = page === 'results';
  onHomepage = page === 'home';
  onShorts = page === 'shorts';
  onVideo = page === 'video';
  onChannel = page === 'channel';
  onSubs = page === 'subscriptions';

  SETTINGS.behaviorIds.forEach(id => setBehaviorAttribute(id, cache[id]));
  setBehaviorAttribute('global_enable', cache.global_enable);

  checkRedirects();

  // Redirect the shorts player
  if (IS_TOP_FRAME && on && onShorts && cache.normalize_shorts) {
    const watchUrl = focusWatchUrlForShort(url);
    if (watchUrl) location.replace(watchUrl);
  }

  // Autofocus the search bar
  if (IS_TOP_FRAME && on && !onVideo && (cache.autofocus_search || cache.search_engine_mode)) {
    const searchBar = qs('input#search');
    if (searchBar && !searchBar.value) {
      searchBar?.focus();
    }
  }

  requestRunDynamicSettings();
}

function updateSetting(id, value) {
  if (!SETTINGS.behaviorIdSet.has(id) &&
      !SETTINGS.automationIds.includes(id)) return;
  cache[id] = value;
  setBehaviorAttribute(id, value);
  persistChanges({ [id]: value });
}
}
