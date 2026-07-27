'use strict';

const assert = require('assert/strict');
const {parseFocusReleaseVersion} = require('./release-version');

assert.deepEqual(parseFocusReleaseVersion('1.0.4.0'), {
    fullVersion: '1.0.4.0',
    displayVersion: '1.0.4',
    releaseTag: 'v1.0.4',
});
assert.deepEqual(parseFocusReleaseVersion('1.2.3.0'), {
    fullVersion: '1.2.3.0',
    displayVersion: '1.2.3',
    releaseTag: 'v1.2.3',
});
assert.deepEqual(parseFocusReleaseVersion('1.2.3.4'), {
    fullVersion: '1.2.3.4',
    displayVersion: '1.2.3.4',
    releaseTag: 'v1.2.3.4',
});
assert.throws(() => parseFocusReleaseVersion('1.0'), /Invalid four-part/);

console.log('PASS: Focus 1.0.4.0 -> display 1.0.4, release tag v1.0.4');
