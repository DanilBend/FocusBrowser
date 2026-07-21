// Copyright 2026 The Focus Browser Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

import * as cr from "../cr";

export const setNtpShortcuts = async (
    shortcutIds: readonly string[],
): Promise<number> => {
    return await cr.sendWithPromise('setNtpShortcuts', [...shortcutIds]);
};
