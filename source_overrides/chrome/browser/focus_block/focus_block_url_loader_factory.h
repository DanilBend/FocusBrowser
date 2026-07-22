// Copyright 2026 Focus Browser contributors
// Use of this source code is governed by the license in the project root.

#ifndef CHROME_BROWSER_FOCUS_BLOCK_FOCUS_BLOCK_URL_LOADER_FACTORY_H_
#define CHROME_BROWSER_FOCUS_BLOCK_FOCUS_BLOCK_URL_LOADER_FACTORY_H_

#include "content/public/browser/content_browser_client.h"

namespace content {
class RenderFrameHost;
}

namespace net {
class IsolationInfo;
}

namespace network {
class URLLoaderFactoryBuilder;
}

class Profile;

namespace focus_block {

// Adds FocusBlock to a network URLLoaderFactory chain. Downloads and DevTools
// factories are deliberately excluded. The proxy checks both the initial URL
// and every redirect before allowing the request to reach the network.
void MaybeProxyURLLoaderFactory(
    Profile* profile,
    content::RenderFrameHost* frame,
    content::ContentBrowserClient::URLLoaderFactoryType type,
    const url::Origin& request_initiator,
    const net::IsolationInfo& isolation_info,
    network::URLLoaderFactoryBuilder& factory_builder);

}  // namespace focus_block

#endif  // CHROME_BROWSER_FOCUS_BLOCK_FOCUS_BLOCK_URL_LOADER_FACTORY_H_
