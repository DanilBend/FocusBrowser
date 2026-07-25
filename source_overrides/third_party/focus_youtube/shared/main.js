// Copyright 2026 The Focus Browser Authors
// Canonical FocusYoutube settings schema. This file is intentionally pure:
// it performs no network or browser API calls and is shared by every surface.

'use strict';

(() => {
  const option = (id, label, description = '', effects = undefined) => ({
    id,
    label,
    description,
    ...(effects ? { effects } : {}),
  });

  const SECTIONS = [
    {
      id: 'general',
      title: 'Общее',
      description: 'Настройки, которые действуют сразу на нескольких страницах YouTube.',
      options: [
        option('remove_all_shorts', 'Скрывать все Shorts', 'Главная, подписки, поиск и рекомендации.'),
        option('remove_video_thumbnails', 'Скрывать миниатюры видео'),
        option('blur_video_thumbnails', 'Размывать миниатюры видео'),
        option('shrink_video_thumbnails', 'Уменьшать миниатюры видео'),
        option('disable_play_on_hover', 'Не запускать превью при наведении'),
        option('search_engine_mode', 'Режим поисковика', 'Оставляет поиск и результаты, убирая отвлекающие элементы.'),
      ],
    },
    {
      id: 'basic',
      title: 'Базовые фильтры',
      description: 'Три самых заметных источника отвлечений.',
      options: [
        option('remove_homepage', 'Скрывать рекомендации на главной'),
        option('remove_sidebar', 'Скрывать рекомендации рядом с видео'),
        option('remove_end_of_video', 'Скрывать рекомендации в конце видео'),
      ],
    },
    {
      id: 'homepage',
      title: 'Главная страница',
      description: 'Лента рекомендаций и её поведение.',
      options: [
        option('add_reveal_homepage', 'Показывать кнопку временного раскрытия ленты'),
        option('remove_header', 'Скрывать фильтры над лентой'),
        option(
            'remove_all_but_one',
            'Оставлять только первый ряд рекомендаций',
            '',
            { true: { remove_homepage: false, remove_extra_rows: true, remove_infinite_scroll: true } }),
        option('remove_extra_rows', 'Скрывать дополнительные ряды'),
        option('remove_infinite_scroll', 'Отключать бесконечную прокрутку главной'),
        option('remove_playables', 'Скрывать Playables'),
      ],
    },
    {
      id: 'left_navigation',
      title: 'Левая панель',
      description: 'Кнопки основной навигации.',
      options: [
        option('remove_left_nav_bar', 'Скрывать всю левую панель', '', { true: { only_show_playlists: false } }),
        option(
            'only_show_playlists',
            'Оставлять в панели только плейлисты',
            '',
            {
              true: {
                remove_left_nav_bar: false,
                remove_quick_links_section: false,
                remove_sub_section: true,
                remove_explore_section: true,
                remove_more_section: true,
                remove_settings_section: true,
                remove_footer_section: true,
                remove_home_link: true,
                remove_explore_link: true,
                remove_shorts_link: true,
                remove_subscriptions_link: true,
              },
            }),
        option('remove_logo_link', 'Отключать переход по логотипу YouTube'),
        option('remove_home_link', 'Скрывать кнопку «Главная»', '', { false: { only_show_playlists: false } }),
        option('remove_explore_link', 'Скрывать кнопку «Навигатор»', '', { false: { only_show_playlists: false } }),
        option('remove_shorts_link', 'Скрывать кнопку Shorts', '', { false: { only_show_playlists: false } }),
        option('remove_subscriptions_link', 'Скрывать кнопку «Подписки»', '', { false: { only_show_playlists: false } }),
      ],
    },
    {
      id: 'left_sections',
      title: 'Разделы левой панели',
      description: 'Группы ссылок внутри раскрытой панели.',
      options: [
        option('remove_sub_section', 'Скрывать раздел подписок', '', { false: { only_show_playlists: false } }),
        option('remove_quick_links_section', 'Скрывать раздел «Вы» и библиотеку', '', { false: { only_show_playlists: false } }),
        option('remove_explore_section', 'Скрывать раздел «Навигатор»', '', { false: { only_show_playlists: false } }),
        option('remove_more_section', 'Скрывать «Другие возможности YouTube»', '', { false: { only_show_playlists: false } }),
        option('remove_settings_section', 'Скрывать системные ссылки', '', { false: { only_show_playlists: false } }),
        option('remove_footer_section', 'Скрывать подвал панели', '', { false: { only_show_playlists: false } }),
      ],
    },
    {
      id: 'player_behavior',
      title: 'Плеер — поведение',
      description: 'Воспроизведение и переходы.',
      options: [
        option('auto_skip_ads', 'Пропускать и ускорять рекламу', 'Работает только когда этот переключатель включён.'),
        option('disable_autoplay', 'Отключать автовоспроизведение'),
        option('disable_ambient_mode', 'Отключать фоновую подсветку'),
        option('disable_annotations', 'Отключать аннотации'),
        option('expand_description', 'Автоматически раскрывать описание'),
        option('disable_fullscreen_scroll', 'Запрещать прокрутку в полноэкранном режиме'),
        option('normalize_shorts', 'Открывать Shorts в обычном плеере'),
        option('enable_theater', 'Автоматически включать широкий режим'),
      ],
    },
    {
      id: 'player_interface',
      title: 'Плеер — интерфейс',
      description: 'Элементы внутри и под видео.',
      options: [
        option('add_reveal_end_of_video', 'Показывать кнопку временного раскрытия финальных рекомендаций'),
        option('remove_info_cards', 'Скрывать информационные карточки'),
        option('remove_overlay_suggestions', 'Скрывать всплывающие подсказки'),
        option('remove_play_next_button', 'Скрывать кнопку следующего видео'),
        option('remove_menu_buttons', 'Скрывать кнопки «Нравится», «Поделиться» и другие'),
        option('remove_clip_button', 'Скрывать кнопку создания клипа'),
        option('remove_video_likes', 'Скрывать число отметок «Нравится»'),
        option('remove_channel_subscribers', 'Скрывать число подписчиков канала'),
        option('remove_vid_description', 'Скрывать описание видео'),
        option('remove_embedded_more_videos', 'Скрывать «Другие видео» во встроенном плеере'),
      ],
    },
    {
      id: 'player_sidebar',
      title: 'Плеер — боковая колонка',
      description: 'Рекомендации, чат и прокрутка справа.',
      options: [
        option('add_reveal_sidebar', 'Показывать кнопку временного раскрытия рекомендаций'),
        option('remove_entire_sidebar', 'Убирать всю боковую колонку'),
        option('remove_sidebar_infinite_scroll', 'Отключать бесконечную прокрутку колонки'),
        option('remove_extra_sidebar_tags', 'Оставлять только основные фильтры'),
        option('remove_chat', 'Скрывать чат трансляции'),
      ],
    },
    {
      id: 'comments',
      title: 'Комментарии',
      description: 'Состав и оформление обсуждения.',
      options: [
        option('remove_comments', 'Скрывать все комментарии'),
        option(
            'remove_non_timestamp_comments',
            'Оставлять только комментарии с таймкодами',
            '',
            { true: { remove_comments: false } }),
        option('remove_comment_usernames', 'Скрывать имена авторов комментариев'),
        option('remove_comment_profiles', 'Скрывать аватары авторов'),
        option('remove_comment_replies', 'Скрывать ответы'),
        option('remove_comment_upvotes', 'Скрывать оценки комментариев'),
      ],
    },
    {
      id: 'search',
      title: 'Результаты поиска',
      description: 'Фильтрация выдачи YouTube.',
      options: [
        option('remove_search_suggestions', 'Скрывать подсказки поиска'),
        option('remove_search_promoted', 'Скрывать продвигаемые видео'),
        option('remove_shorts_results', 'Скрывать Shorts в результатах'),
        option('remove_results_description', 'Скрывать описания результатов'),
        option('remove_extra_results', 'Скрывать дополнительные блоки выдачи'),
        option('remove_thumbnail_mouseover_effect', 'Отключать слайд-шоу миниатюр'),
        option('remove_infinite_scroll_search', 'Отключать бесконечную прокрутку поиска'),
      ],
    },
    {
      id: 'channel',
      title: 'Страница канала',
      description: 'Автозапуск и разделы каналов.',
      options: [
        option('disable_channel_autoplay', 'Не запускать трейлер канала'),
        option('remove_channel_for_you', 'Скрывать раздел «Для вас»'),
        option('reverse_channel_video_list', 'Показывать старые видео первыми'),
      ],
    },
    {
      id: 'subscriptions',
      title: 'Страница подписок',
      description: 'Фильтры контента в подписках.',
      options: [
        option('remove_sub_shorts', 'Скрывать Shorts'),
        option('remove_sub_most_relevant', 'Скрывать раздел «Самое актуальное»'),
        option('remove_sub_live', 'Скрывать прямые эфиры'),
        option('remove_sub_upcoming', 'Скрывать будущие трансляции'),
        option('remove_sub_premiere', 'Скрывать премьеры'),
        option('remove_sub_vods', 'Скрывать записи трансляций'),
      ],
    },
    {
      id: 'redirects',
      title: 'Переход с главной',
      description: 'Варианты взаимоисключающие.',
      options: [
        option(
            'redirect_to_subs',
            'Открывать подписки вместо главной',
            '',
            { true: { redirect_to_wl: false, redirect_to_library: false, redirect_off: false } }),
        option(
            'redirect_to_wl',
            'Открывать «Смотреть позже» вместо главной',
            '',
            { true: { redirect_to_subs: false, redirect_to_library: false, redirect_off: false } }),
        option(
            'redirect_to_library',
            'Открывать библиотеку вместо главной',
            '',
            { true: { redirect_to_subs: false, redirect_to_wl: false, redirect_off: false } }),
        option(
            'redirect_off',
            'Не перенаправлять главную',
            '',
            { true: { redirect_to_subs: false, redirect_to_wl: false, redirect_to_library: false } }),
      ],
    },
    {
      id: 'other',
      title: 'Дополнительно',
      description: 'Редкие, но полезные режимы.',
      options: [
        option('remove_playlist_suggestions', 'Скрывать рекомендации плейлистов'),
        option('remove_notif_bell', 'Скрывать уведомления и счётчик во вкладке'),
        option('autofocus_search', 'Сразу устанавливать курсор в поиск'),
        option('remove_context', 'Скрывать информационные панели'),
        option('grayscale_mode', 'Делать YouTube чёрно-белым'),
        option('menu_timer', 'Задерживать доступ к настройкам на 10 секунд', '', { true: { lock_code: false } }),
        option('lock_code', 'Требовать ввод случайного кода', '', { true: { menu_timer: false } }),
      ],
    },
    {
      id: 'focus_navigation',
      title: 'Focus — страницы',
      description: 'Дополнительные встроенные фильтры Focus Browser.',
      options: [
        option('remove_subscriptions_page', 'Скрывать содержимое страницы подписок'),
        option('remove_trending_page', 'Скрывать страницы «Навигатор» и трендов'),
      ],
    },
    {
      id: 'focus_video',
      title: 'Focus — страница видео',
      description: 'Точные фильтры отдельных блоков под видео.',
      options: [
        option('remove_channel_owner', 'Скрывать блок автора канала'),
        option('remove_donation_shelf', 'Скрывать сборы средств'),
        option('remove_top_header', 'Скрывать верхнюю панель YouTube'),
        option('remove_merch_shelves', 'Скрывать товары, билеты и предложения'),
        option('remove_video_metadata', 'Скрывать сведения о видео'),
        option('remove_mixes', 'Скрывать миксы'),
        option('remove_playlist_panel', 'Скрывать панель плейлиста'),
      ],
    },
  ];

  const BEHAVIOR_IDS = SECTIONS.flatMap(section =>
    section.options.map(entry => entry.id));
  const BEHAVIOR_ID_SET = new Set(BEHAVIOR_IDS);
  const BEHAVIOR_DEFAULTS = Object.fromEntries(
      BEHAVIOR_IDS.map(id => [id, false]));
  // Schema v4 exposes the original Unhook 1.6.9 controls in the compact
  // browser-owned surface. Every option outside this set remains available to
  // the compatibility engine but is disabled during migration.
  const NATIVE_BEHAVIOR_IDS = Object.freeze([
    'remove_homepage',
    'remove_entire_sidebar',
    'remove_sidebar',
    'remove_chat',
    'remove_playlist_panel',
    'remove_end_of_video',
    'remove_info_cards',
    'remove_comments',
    'remove_comment_profiles',
    'remove_mixes',
    'remove_merch_shelves',
    'remove_video_metadata',
    'remove_menu_buttons',
    'remove_channel_owner',
    'remove_vid_description',
    'remove_top_header',
    'remove_notif_bell',
    'remove_extra_results',
    'remove_explore_link',
    'remove_explore_section',
    'remove_trending_page',
    'remove_more_section',
    'remove_all_shorts',
    'remove_subscriptions_link',
    'remove_sub_section',
    'remove_subscriptions_page',
    'redirect_to_subs',
    'disable_autoplay',
    'disable_annotations',
  ]);
  const NATIVE_BEHAVIOR_ID_SET = new Set(NATIVE_BEHAVIOR_IDS);
  const CURRENT_SCHEMA_VERSION = 4;
  const REDIRECT_IDS = Object.freeze([
    'redirect_to_subs',
    'redirect_to_wl',
    'redirect_to_library',
    'redirect_off',
  ]);
  const AUTOMATION_IDS = Object.freeze([
    'global_enable',
    'schedule',
    'scheduleTimes',
    'scheduleDays',
    'nextTimedChange',
    'nextTimedValue',
  ]);

  const META_DEFAULTS = {
    global_enable: true,
    dark_mode: true,
    schedule: false,
    scheduleTimes: '09:00-17:00',
    scheduleDays: 'mo,tu,we,th,fr',
    nextTimedChange: false,
    nextTimedValue: true,
    password: false,
    hashed_password: '',
    focus_youtube_schema_version: CURRENT_SCHEMA_VERSION,
  };
  const DEFAULT_SETTINGS = Object.freeze({
    ...BEHAVIOR_DEFAULTS,
    ...META_DEFAULTS,
  });

  const LEGACY_MAPPINGS = Object.freeze({
    hide_annotations: ['disable_annotations'],
    hide_autoplay: ['disable_autoplay'],
    hide_bar: ['remove_menu_buttons'],
    hide_cards: ['remove_info_cards'],
    hide_chat: ['remove_chat'],
    hide_comments: ['remove_comments'],
    hide_desc: ['remove_vid_description'],
    hide_endscreen: ['remove_end_of_video'],
    hide_feed: ['remove_homepage'],
    hide_moreyt: ['remove_more_section'],
    hide_notifs: ['remove_notif_bell'],
    hide_prof: ['remove_comment_profiles'],
    hide_recommended: ['remove_sidebar'],
    hide_redirect_home: ['redirect_to_subs'],
    hide_shorts: ['remove_all_shorts'],
    hide_sidebar: ['remove_entire_sidebar'],
    hide_search: ['remove_extra_results'],
    hide_subs: [
      'remove_subscriptions_link',
      'remove_sub_section',
      'remove_subscriptions_page',
    ],
    hide_trending: [
      'remove_explore_link',
      'remove_explore_section',
      'remove_trending_page',
    ],
    hide_channel: ['remove_channel_owner'],
    hide_donate: ['remove_donation_shelf'],
    hide_header: ['remove_top_header'],
    hide_merch: ['remove_merch_shelves'],
    hide_meta: ['remove_video_metadata'],
    hide_mix: ['remove_mixes'],
    hide_playlists: ['remove_playlist_panel'],
  });

  const LEGACY_KEYS = Object.freeze([
    'yt_on',
    'popup_settings',
    ...Object.keys(LEGACY_MAPPINGS),
  ]);
  const FORBIDDEN_STORAGE_KEYS = Object.freeze([
    'session_token',
    'license_token',
    'user_email',
    'log_enabled',
    'log_prompt_answered',
  ]);

  const ID_TO_SHORT_ID = Object.freeze({
    global_enable: '0',
    dark_mode: '1',
    remove_homepage: '3',
    remove_sidebar: '4',
    remove_end_of_video: '5',
    remove_header: '6',
    remove_all_but_one: '7',
    remove_extra_rows: '8',
    remove_infinite_scroll: '9',
    disable_play_on_hover: '10',
    remove_left_nav_bar: '11',
    remove_logo_link: '12',
    remove_home_link: '13',
    remove_explore_link: '14',
    remove_shorts_link: '15',
    remove_subscriptions_link: '16',
    remove_quick_links_section: '17',
    remove_sub_section: '18',
    remove_explore_section: '19',
    remove_more_section: '20',
    auto_skip_ads: '21',
    disable_autoplay: '22',
    disable_ambient_mode: '23',
    disable_annotations: '24',
    normalize_shorts: '26',
    remove_entire_sidebar: '27',
    remove_info_cards: '28',
    remove_overlay_suggestions: '29',
    remove_play_next_button: '30',
    remove_menu_buttons: '31',
    remove_chat: '32',
    remove_embedded_more_videos: '33',
    remove_comments: '34',
    remove_non_timestamp_comments: '35',
    remove_comment_usernames: '36',
    remove_comment_profiles: '37',
    remove_comment_replies: '38',
    remove_comment_upvotes: '39',
    remove_search_suggestions: '40',
    remove_search_promoted: '41',
    remove_shorts_results: '42',
    remove_extra_results: '43',
    remove_thumbnail_mouseover_effect: '44',
    remove_sub_shorts: '45',
    remove_sub_live: '46',
    remove_sub_upcoming: '47',
    remove_sub_premiere: '48',
    redirect_to_subs: '49',
    redirect_to_wl: '50',
    redirect_to_library: '51',
    redirect_off: '52',
    menu_timer: '53',
    remove_all_shorts: '54',
    remove_video_thumbnails: '55',
    search_engine_mode: '56',
    remove_notif_bell: '57',
    schedule: '58',
    scheduleTimes: '59',
    scheduleDays: '60',
    nextTimedChange: '61',
    nextTimedValue: '62',
    remove_settings_section: '63',
    remove_footer_section: '64',
    remove_infinite_scroll_search: '65',
    autofocus_search: '66',
    remove_playlist_suggestions: '67',
    reverse_channel_video_list: '68',
    expand_description: '69',
    remove_context: '70',
    remove_sub_vods: '71',
    disable_channel_autoplay: '72',
    remove_vid_description: '73',
    blur_video_thumbnails: '74',
    remove_extra_sidebar_tags: '75',
    remove_video_likes: '76',
    remove_channel_for_you: '77',
    disable_fullscreen_scroll: '78',
    only_show_playlists: '79',
    remove_channel_subscribers: '80',
    grayscale_mode: '81',
    lock_code: '82',
    remove_clip_button: '83',
    shrink_video_thumbnails: '84',
    add_reveal_homepage: '87',
    add_reveal_sidebar: '88',
    add_reveal_end_of_video: '89',
    remove_sidebar_infinite_scroll: '91',
    remove_results_description: '93',
    enable_theater: '94',
    remove_playables: '95',
    remove_sub_most_relevant: '96',
    remove_subscriptions_page: '97',
    remove_trending_page: '98',
    remove_channel_owner: '99',
    remove_donation_shelf: '100',
    remove_top_header: '101',
    remove_merch_shelves: '102',
    remove_video_metadata: '103',
    remove_mixes: '104',
    remove_playlist_panel: '105',
  });
  const SHORT_ID_TO_ID = Object.freeze(Object.fromEntries(
      Object.entries(ID_TO_SHORT_ID).map(([id, shortId]) => [shortId, id])));

  const hasOwn = (object, key) =>
    Object.prototype.hasOwnProperty.call(object, key);

  function isValidValue(id, value) {
    if (BEHAVIOR_ID_SET.has(id)) return typeof value === 'boolean';
    switch (id) {
      case 'global_enable':
      case 'dark_mode':
      case 'schedule':
      case 'nextTimedValue':
      case 'password':
        return typeof value === 'boolean';
      case 'nextTimedChange':
        return value === false ||
            (typeof value === 'number' && Number.isFinite(value) && value > 0);
      case 'scheduleTimes':
      case 'scheduleDays':
      case 'hashed_password':
        return typeof value === 'string';
      case 'focus_youtube_schema_version':
        return Number.isInteger(value) && value >= 0;
      default:
        return false;
    }
  }

  function createMigration(savedValue = {}) {
    const saved = savedValue && typeof savedValue === 'object' ?
      savedValue : {};
    const patch = {};
    const savedSchemaVersion =
        Number.isInteger(saved.focus_youtube_schema_version) &&
            saved.focus_youtube_schema_version >= 0 ?
          saved.focus_youtube_schema_version : 0;

    if (!hasOwn(saved, 'global_enable') &&
        typeof saved.yt_on === 'boolean') {
      patch.global_enable = saved.yt_on;
    }
    if (!hasOwn(saved, 'dark_mode') &&
        saved.popup_settings &&
        typeof saved.popup_settings === 'object' &&
        typeof saved.popup_settings.dark_mode === 'boolean') {
      patch.dark_mode = saved.popup_settings.dark_mode;
    }
    if (saved.scheduleTimes === '9:00a-5:00p') {
      patch.scheduleTimes = '09:00-17:00';
    }

    for (const [legacyId, targetIds] of Object.entries(LEGACY_MAPPINGS)) {
      if (typeof saved[legacyId] !== 'boolean') continue;
      for (const targetId of targetIds) {
        if (!hasOwn(saved, targetId) && !hasOwn(patch, targetId)) {
          patch[targetId] = saved[legacyId];
        }
      }
    }

    if (savedSchemaVersion < CURRENT_SCHEMA_VERSION) {
      for (const id of BEHAVIOR_IDS) {
        if (!NATIVE_BEHAVIOR_ID_SET.has(id)) {
          patch[id] = false;
          continue;
        }
        // Preserve a canonical value first, then a value recovered from a
        // legacy key. Invalid or absent values use the safe disabled default.
        patch[id] = typeof saved[id] === 'boolean' ? saved[id] :
          typeof patch[id] === 'boolean' ? patch[id] : false;
      }
      patch.schedule = false;
      patch.nextTimedChange = false;
      patch.nextTimedValue = true;
      patch.password = false;
      patch.hashed_password = '';
      patch.focus_youtube_schema_version = CURRENT_SCHEMA_VERSION;
    }

    for (const [id, defaultValue] of Object.entries(DEFAULT_SETTINGS)) {
      if (hasOwn(patch, id)) continue;
      if (!hasOwn(saved, id) || !isValidValue(id, saved[id])) {
        patch[id] = defaultValue;
      }
    }
    const settings = { ...DEFAULT_SETTINGS };
    for (const [id, value] of Object.entries(saved)) {
      if (isValidValue(id, value)) settings[id] = value;
    }
    Object.assign(settings, patch);

    // Corrupt or legacy imports must never trigger competing navigations.
    // The safest recovery is to disable every redirect and let the user pick
    // one explicitly in the FocusYoutube panel.
    if (REDIRECT_IDS.filter(id => settings[id] === true).length > 1) {
      for (const id of REDIRECT_IDS) {
        settings[id] = false;
        patch[id] = false;
      }
    }

    const removeKeys = [...LEGACY_KEYS, ...FORBIDDEN_STORAGE_KEYS]
        .filter(key => hasOwn(saved, key));
    return { settings, patch, removeKeys };
  }

  function applyEffects(settings, id, value) {
    const entry = SECTIONS
        .flatMap(section => section.options)
        .find(candidate => candidate.id === id);
    const result = { [id]: value };
    const effects = entry?.effects?.[String(value)];
    if (effects) Object.assign(result, effects);
    return result;
  }

  globalThis.FocusYoutubeSettings = Object.freeze({
    sections: SECTIONS,
    behaviorIds: BEHAVIOR_IDS,
    behaviorIdSet: BEHAVIOR_ID_SET,
    behaviorDefaults: Object.freeze(BEHAVIOR_DEFAULTS),
    nativeBehaviorIds: NATIVE_BEHAVIOR_IDS,
    automationIds: AUTOMATION_IDS,
    metaDefaults: Object.freeze(META_DEFAULTS),
    defaults: DEFAULT_SETTINGS,
    legacyMappings: LEGACY_MAPPINGS,
    idToShortId: ID_TO_SHORT_ID,
    shortIdToId: SHORT_ID_TO_ID,
    createMigration,
    applyEffects,
    isValidValue,
  });
})();
