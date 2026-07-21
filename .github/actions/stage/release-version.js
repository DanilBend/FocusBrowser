'use strict';

function parseFocusReleaseVersion(value) {
    const fullVersion = String(value).trim();
    if (!/^\d+\.\d+\.\d+\.\d+$/.test(fullVersion)) {
        throw new Error(`Invalid four-part Focus version: ${fullVersion}`);
    }

    const parts = fullVersion.split('.');
    const displayParts = [...parts];
    while (displayParts.length > 2 && displayParts.at(-1) === '0') {
        displayParts.pop();
    }

    // The fourth component is a platform packaging revision. Keep it in the
    // tag only when non-zero, avoiding collisions for future platform rebuilds.
    const tagParts = parts[3] === '0' ? parts.slice(0, 3) : parts;
    return {
        fullVersion,
        displayVersion: displayParts.join('.'),
        releaseTag: `v${tagParts.join('.')}`,
    };
}

module.exports = {parseFocusReleaseVersion};
