/*
 * Copyright (c) 2026 Focus Browser contributors.
 *
 * This adapter is part of the Focus Browser project. The bundled Ghostery
 * engine remains covered by its upstream MPL-2.0 license and notices.
 */

import { ENGINE_VERSION, FiltersEngine, Request } from '@ghostery/adblocker';

export const UPSTREAM_NAME = '@ghostery/adblocker';
export const UPSTREAM_VERSION = '2.18.1';
export const UPSTREAM_COMMIT = '67ef23276e93ebc5dd4621cc9df2b09ad9f490d7';

const DEFAULT_CONFIG = Object.freeze({
  debug: false,
  enableCompression: false,
  enableHtmlFiltering: false,
  enableInMemoryCache: true,
  enableMutationObserver: false,
  enableOptimizations: true,
  enablePushInjectionsOnNavigationEvents: false,
  guessRequestTypeFromUrl: false,
  integrityCheck: true,
  loadCSPFilters: false,
  loadCosmeticFilters: true,
  loadExceptionFilters: true,
  loadExtendedSelectors: false,
  loadGenericCosmeticsFilters: true,
  loadNetworkFilters: true,
  loadPreprocessors: false,
});

const CONFIG_KEYS = new Set(Object.keys(DEFAULT_CONFIG));
const REQUEST_TYPE_ALIASES = new Map([
  ['document', 'main_frame'],
  ['frame', 'sub_frame'],
  ['iframe', 'sub_frame'],
  ['subdocument', 'sub_frame'],
  ['style', 'stylesheet'],
  ['worker', 'script'],
  ['serviceworker', 'script'],
  ['sharedworker', 'script'],
  ['fetch', 'xmlhttprequest'],
  ['xhr', 'xmlhttprequest'],
]);

const KNOWN_REQUEST_TYPES = new Set([
  'main_frame',
  'sub_frame',
  'stylesheet',
  'script',
  'image',
  'font',
  'object',
  'xmlhttprequest',
  'ping',
  'csp_report',
  'media',
  'websocket',
  'other',
  'beacon',
  'json',
  'manifest',
]);

const MAX_COSMETIC_NAMES = 4096;
let engine = null;

function requireEngine() {
  if (engine === null) {
    throw new Error('Ghostery adblocker engine is not initialized');
  }
  return engine;
}

function normalizeConfig(overrides = {}) {
  if (overrides === null || typeof overrides !== 'object' || Array.isArray(overrides)) {
    throw new TypeError('config must be an object');
  }

  const config = { ...DEFAULT_CONFIG };
  for (const [key, value] of Object.entries(overrides)) {
    if (!CONFIG_KEYS.has(key)) {
      throw new TypeError(`unsupported Ghostery config key: ${key}`);
    }
    if (typeof value !== 'boolean') {
      throw new TypeError(`Ghostery config value must be boolean: ${key}`);
    }
    config[key] = value;
  }
  return config;
}

function normalizeFilterText(value) {
  if (typeof value === 'string') {
    return value;
  }
  if (Array.isArray(value) && value.every((item) => typeof item === 'string')) {
    return value.join('\n');
  }
  throw new TypeError('filterText must be a string or an array of strings');
}

function serializedBytes(value) {
  if (value instanceof Uint8Array) {
    return value;
  }
  if (value instanceof ArrayBuffer) {
    return new Uint8Array(value);
  }
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  if (Array.isArray(value)) {
    return Uint8Array.from(value);
  }
  if (typeof value === 'string') {
    if (typeof globalThis.atob !== 'function') {
      throw new TypeError('base64 serialized input requires globalThis.atob');
    }
    const binary = globalThis.atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; ++index) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  }
  throw new TypeError('serialized engine must be bytes, an ArrayBuffer, an array, or base64');
}

function applyResources(target, resourcesText, resourcesChecksum) {
  if (resourcesText === undefined || resourcesText === null) {
    return;
  }
  if (typeof resourcesText !== 'string') {
    throw new TypeError('resourcesText must be a string');
  }
  if (typeof resourcesChecksum !== 'string' || resourcesChecksum.length === 0) {
    throw new TypeError('resourcesChecksum is required when resourcesText is supplied');
  }
  target.updateResources(resourcesText, resourcesChecksum);
}

export function initializeFromFilterText(filterText, options = {}) {
  const next = FiltersEngine.parse(
    normalizeFilterText(filterText),
    normalizeConfig(options.config),
  );
  applyResources(next, options.resourcesText, options.resourcesChecksum);
  engine = next;
  return status();
}

export function initializeFromSerialized(serialized, options = {}) {
  if (options.config !== undefined) {
    throw new TypeError('serialized engines contain their config; config overrides are not accepted');
  }
  const next = FiltersEngine.deserialize(serializedBytes(serialized));
  applyResources(next, options.resourcesText, options.resourcesChecksum);
  engine = next;
  return status();
}

export function initialize(options = {}) {
  const hasFilters = options.filterText !== undefined;
  const hasSerialized = options.serialized !== undefined;
  if (hasFilters === hasSerialized) {
    throw new TypeError('initialize requires exactly one of filterText or serialized');
  }
  if (hasSerialized) {
    return initializeFromSerialized(options.serialized, options);
  }
  return initializeFromFilterText(options.filterText, options);
}

export function reset() {
  engine = null;
}

export function serialize() {
  return requireEngine().serialize();
}

function normalizedRequestType(value) {
  const lowered = typeof value === 'string' ? value.toLowerCase() : 'other';
  const aliased = REQUEST_TYPE_ALIASES.get(lowered) ?? lowered;
  return KNOWN_REQUEST_TYPES.has(aliased) ? aliased : 'other';
}

function optionalString(value) {
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function requestFromRawDetails(details = {}) {
  if (details === null || typeof details !== 'object' || Array.isArray(details)) {
    throw new TypeError('request details must be an object');
  }
  if (typeof details.url !== 'string' || details.url.length === 0) {
    throw new TypeError('request url is required');
  }

  return Request.fromRawDetails({
    requestId: String(details.requestId ?? '0'),
    tabId: Number.isInteger(details.tabId) ? details.tabId : 0,
    url: details.url,
    hostname: optionalString(details.hostname),
    domain: optionalString(details.domain),
    sourceUrl: typeof details.sourceUrl === 'string' ? details.sourceUrl : '',
    sourceHostname: optionalString(details.sourceHostname),
    sourceDomain: optionalString(details.sourceDomain),
    type: normalizedRequestType(details.type),
  });
}

function filterText(filter) {
  if (filter === undefined || filter === null || typeof filter.getFilter !== 'function') {
    return null;
  }
  try {
    return filter.getFilter();
  } catch {
    return null;
  }
}

export function matchRawDetails(details, options = {}) {
  try {
    const request = requestFromRawDetails(details);
    const result = requireEngine().match(request, options.withMetadata === true);
    return {
      validInput: true,
      matched: result.match === true,
      hasException: result.exception !== undefined,
      redirect: result.redirect === undefined
        ? null
        : {
            filename: result.redirect.filename,
            body: result.redirect.body,
            contentType: result.redirect.contentType,
            dataUrl: result.redirect.dataUrl,
          },
      rewrittenUrl: result.rewrite?.url ?? null,
      matchedFilter: filterText(result.filter),
      exceptionFilter: filterText(result.exception),
      metadata: options.withMetadata === true ? (result.metadata ?? null) : null,
      error: null,
    };
  } catch (error) {
    return {
      validInput: false,
      matched: false,
      hasException: false,
      redirect: null,
      rewrittenUrl: null,
      matchedFilter: null,
      exceptionFilter: null,
      metadata: null,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

function boundedStringArray(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter((item) => typeof item === 'string')
    .slice(0, MAX_COSMETIC_NAMES);
}

export function cosmeticsRawDetails(details = {}, options = {}) {
  try {
    const request = requestFromRawDetails({
      ...details,
      type: details.type ?? 'main_frame',
    });
    const allowScriptlets = options.allowScriptlets === true;
    const allowExtended = options.allowExtended === true;
    const result = requireEngine().getCosmeticsFilters({
      url: request.url,
      hostname: request.hostname,
      domain: request.domain,
      ancestors: Array.isArray(details.ancestors) ? details.ancestors : undefined,
      classes: boundedStringArray(details.classes),
      hrefs: boundedStringArray(details.hrefs),
      ids: boundedStringArray(details.ids),
      getBaseRules: options.getBaseRules !== false,
      getInjectionRules: allowScriptlets,
      getExtendedRules: allowExtended,
      getRulesFromDOM: options.getRulesFromDOM !== false,
      getRulesFromHostname: options.getRulesFromHostname !== false,
      hidingStyle: typeof options.hidingStyle === 'string' ? options.hidingStyle : undefined,
    });
    return {
      validInput: true,
      active: result.active === true,
      styles: typeof result.styles === 'string' ? result.styles : '',
      scripts: allowScriptlets && Array.isArray(result.scripts) ? result.scripts : [],
      extended: allowExtended && Array.isArray(result.extended) ? result.extended : [],
      error: null,
    };
  } catch (error) {
    return {
      validInput: false,
      active: false,
      styles: '',
      scripts: [],
      extended: [],
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

export function status() {
  return {
    initialized: engine !== null,
    upstreamName: UPSTREAM_NAME,
    upstreamVersion: UPSTREAM_VERSION,
    upstreamCommit: UPSTREAM_COMMIT,
    engineVersion: ENGINE_VERSION,
  };
}
