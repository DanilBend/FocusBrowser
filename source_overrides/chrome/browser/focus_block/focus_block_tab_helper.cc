// Copyright 2026 Focus Browser contributors
// Use of this source code is governed by the license in the project root.

#include "chrome/browser/focus_block/focus_block_tab_helper.h"

#include "base/functional/bind.h"
#include "base/functional/callback_helpers.h"
#include "base/json/json_writer.h"
#include "base/strings/strcat.h"
#include "base/strings/utf_string_conversions.h"
#include "chrome/browser/focus_block/focus_block_service_factory.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/common/chrome_isolated_world_ids.h"
#include "content/public/browser/page.h"
#include "content/public/browser/render_frame_host.h"
#include "content/public/browser/web_contents.h"

namespace focus_block {
namespace {

// The isolated world has its own global object, while still sharing the page
// DOM. Keeping the owned style node in that private global avoids selecting,
// overwriting, or deleting a page-authored element with a look-alike marker.
constexpr char kStyleSlot[] = "__focusBrowserNativeBlockerStyle_1_0";
GURL TopLevelUrlForFrame(content::RenderFrameHost* render_frame_host) {
  content::RenderFrameHost* outermost_main_frame =
      render_frame_host ? render_frame_host->GetOutermostMainFrame() : nullptr;
  return outermost_main_frame ? outermost_main_frame->GetLastCommittedURL()
                              : GURL();
}

}  // namespace

FocusBlockTabHelper::FocusBlockTabHelper(content::WebContents* web_contents)
    : content::WebContentsObserver(web_contents),
      content::WebContentsUserData<FocusBlockTabHelper>(*web_contents) {
  Profile* profile =
      Profile::FromBrowserContext(web_contents->GetBrowserContext());
  if (profile) {
    service_ = FocusBlockServiceFactory::GetForProfile(profile);
  }
  if (service_) {
    service_->AddObserver(this);
  }
}

FocusBlockTabHelper::~FocusBlockTabHelper() {
  if (service_) {
    service_->RemoveObserver(this);
  }
}

void FocusBlockTabHelper::DOMContentLoaded(
    content::RenderFrameHost* render_frame_host) {
  ApplyToFrame(render_frame_host);
}

void FocusBlockTabHelper::PrimaryPageChanged(content::Page& page) {
  // A BFCache restore or prerender activation swaps in an already-loaded page,
  // so DOMContentLoaded will not fire again. Reapply (or clear) protection for
  // the newly primary frame tree using the current preference state.
  page.GetMainDocument().ForEachRenderFrameHost(
      [this](content::RenderFrameHost* render_frame_host) {
        if (render_frame_host->IsRenderFrameLive() &&
            render_frame_host->IsDOMContentLoaded()) {
          ApplyToFrame(render_frame_host);
        }
      });
}

void FocusBlockTabHelper::OnFocusBlockStateChanged() {
  content::RenderFrameHost* main_frame =
      web_contents() ? web_contents()->GetPrimaryMainFrame() : nullptr;
  if (!main_frame) {
    return;
  }
  main_frame->ForEachRenderFrameHost(
      [this](content::RenderFrameHost* render_frame_host) {
        if (!render_frame_host->IsRenderFrameLive() ||
            !render_frame_host->IsDOMContentLoaded()) {
          return;
        }
        ApplyToFrame(render_frame_host);
      });
}

void FocusBlockTabHelper::OnFocusBlockServiceShuttingDown() {
  service_ = nullptr;
}

void FocusBlockTabHelper::ApplyToFrame(
    content::RenderFrameHost* render_frame_host) {
  if (!service_ || !web_contents() || !render_frame_host ||
      !render_frame_host->IsRenderFrameLive()) {
    return;
  }

  service_->GetCosmeticResourcesForUrl(
      render_frame_host->GetLastCommittedURL(),
      TopLevelUrlForFrame(render_frame_host), {}, {},
      base::BindOnce(&FocusBlockTabHelper::OnInitialCosmetics,
                     weak_ptr_factory_.GetWeakPtr(),
                     render_frame_host->GetWeakDocumentPtr()));
}

void FocusBlockTabHelper::ClearFrame(
    content::RenderFrameHost* render_frame_host) {
  if (!render_frame_host || !render_frame_host->IsRenderFrameLive()) {
    return;
  }
  const std::string script = base::StrCat(
      {"(() => { const style = globalThis['", kStyleSlot,
       "']; if (style && typeof style.remove === 'function') style.remove(); "
       "delete globalThis['",
       kStyleSlot, "']; })();"});
  render_frame_host->ExecuteJavaScriptInIsolatedWorld(
      base::UTF8ToUTF16(script), base::DoNothing(),
      ISOLATED_WORLD_ID_CHROME_INTERNAL);
}

void FocusBlockTabHelper::OnInitialCosmetics(
    content::WeakDocumentPtr document,
    std::optional<FocusBlockService::CosmeticResources> resources) {
  content::RenderFrameHost* render_frame_host =
      document.AsRenderFrameHostIfValid();
  if (!render_frame_host || !render_frame_host->IsRenderFrameLive() ||
      content::WebContents::FromRenderFrameHost(render_frame_host) !=
          web_contents()) {
    return;
  }
  if (!resources) {
    ClearFrame(render_frame_host);
    return;
  }
  render_frame_host->ExecuteJavaScriptInIsolatedWorld(
      BuildApplyCssScript(resources->css, /*collect_element_names=*/true),
      base::BindOnce(&FocusBlockTabHelper::OnElementNamesCollected,
                     weak_ptr_factory_.GetWeakPtr(), document),
      ISOLATED_WORLD_ID_CHROME_INTERNAL);
}

void FocusBlockTabHelper::OnElementNamesCollected(
    content::WeakDocumentPtr document,
    base::Value result) {
  if (!service_ || !result.is_dict()) {
    return;
  }
  content::RenderFrameHost* render_frame_host =
      document.AsRenderFrameHostIfValid();
  if (!render_frame_host || !render_frame_host->IsRenderFrameLive() ||
      content::WebContents::FromRenderFrameHost(render_frame_host) !=
          web_contents()) {
    return;
  }
  std::vector<std::string> classes;
  std::vector<std::string> ids;
  if (const base::ListValue* class_values =
          result.GetDict().FindList("classes")) {
    for (const base::Value& value : *class_values) {
      if (value.is_string()) {
        classes.push_back(value.GetString());
      }
    }
  }
  if (const base::ListValue* id_values =
          result.GetDict().FindList("ids")) {
    for (const base::Value& value : *id_values) {
      if (value.is_string()) {
        ids.push_back(value.GetString());
      }
    }
  }

  service_->GetCosmeticResourcesForUrl(
      render_frame_host->GetLastCommittedURL(),
      TopLevelUrlForFrame(render_frame_host), classes, ids,
      base::BindOnce(&FocusBlockTabHelper::OnFinalCosmetics,
                     weak_ptr_factory_.GetWeakPtr(), document));
}

void FocusBlockTabHelper::OnFinalCosmetics(
    content::WeakDocumentPtr document,
    std::optional<FocusBlockService::CosmeticResources> resources) {
  content::RenderFrameHost* render_frame_host =
      document.AsRenderFrameHostIfValid();
  if (!render_frame_host || !render_frame_host->IsRenderFrameLive() ||
      content::WebContents::FromRenderFrameHost(render_frame_host) !=
          web_contents()) {
    return;
  }
  if (!resources) {
    ClearFrame(render_frame_host);
    return;
  }
  render_frame_host->ExecuteJavaScriptInIsolatedWorld(
      BuildApplyCssScript(resources->css, /*collect_element_names=*/false),
      base::DoNothing(), ISOLATED_WORLD_ID_CHROME_INTERNAL);
}

std::u16string FocusBlockTabHelper::BuildApplyCssScript(
    const std::string& css,
    bool collect_element_names) const {
  const std::string css_literal =
      base::WriteJson(base::Value(css)).value_or("\"\"");
  std::string script = base::StrCat(
      {"(() => { let style = globalThis['", kStyleSlot,
       "']; if (!style || style.tagName !== 'STYLE') { "
       "style = document.createElement('style'); globalThis['",
       kStyleSlot,
       "'] = style; const parent = document.head || document.documentElement; "
       "if (!parent) return {classes: [], ids: []}; parent.appendChild(style); "
       "} else if (!style.isConnected) { const parent = document.head || "
       "document.documentElement; if (!parent) return {classes: [], ids: []}; "
       "parent.appendChild(style); } style.textContent = ",
       css_literal, ";"});
  if (collect_element_names) {
    script.append(
        "const classes = new Set(), ids = new Set(); "
        "const nodes = document.querySelectorAll('[class],[id]'); "
        "const limit = Math.min(nodes.length, 4096); "
        "for (let i = 0; i < limit; ++i) { const node = nodes[i]; "
        "if (node.id && ids.size < 4096) ids.add(node.id); "
        "for (const name of node.classList) { if (classes.size >= 4096) "
        "break; classes.add(name); } } "
        "return {classes: Array.from(classes), ids: Array.from(ids)};");
  }
  script.append("})();");
  return base::UTF8ToUTF16(script);
}

WEB_CONTENTS_USER_DATA_KEY_IMPL(FocusBlockTabHelper);

}  // namespace focus_block
