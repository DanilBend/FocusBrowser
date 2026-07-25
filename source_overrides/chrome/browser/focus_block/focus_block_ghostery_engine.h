// Copyright 2026 Focus Browser contributors
// Use of this source code is governed by the license in the project root.

#ifndef CHROME_BROWSER_FOCUS_BLOCK_FOCUS_BLOCK_GHOSTERY_ENGINE_H_
#define CHROME_BROWSER_FOCUS_BLOCK_FOCUS_BLOCK_GHOSTERY_ENGINE_H_

#include <memory>
#include <string>
#include <vector>

namespace focus_block {

// Plain cross-sequence request/result types used by the browser-owned
// Ghostery engine. They intentionally contain no browser, Mojo, or V8 state.
struct GhosteryMatchRequest {
  std::string url;
  std::string source_url;
  std::string type;
};

struct GhosteryMatchResult {
  bool valid_input = false;
  bool matched = false;
  std::string error;
};

struct GhosteryCosmeticRequest {
  std::string url;
  std::vector<std::string> classes;
  std::vector<std::string> ids;
};

struct GhosteryCosmeticResult {
  bool valid_input = false;
  bool active = false;
  std::string styles;
  std::string error;
};

// Owns a private V8 isolate and the checked-in @ghostery/adblocker bundle.
// Instances must be constructed, called, and destroyed on one dedicated
// single-thread task runner. FocusBlockService enforces that with
// base::SequenceBound.
class FocusBlockGhosteryEngine {
 public:
  FocusBlockGhosteryEngine(std::string bundle_source,
                           std::string filter_text);
  ~FocusBlockGhosteryEngine();

  FocusBlockGhosteryEngine(const FocusBlockGhosteryEngine&) = delete;
  FocusBlockGhosteryEngine& operator=(const FocusBlockGhosteryEngine&) =
      delete;

  bool IsReady() const;
  GhosteryMatchResult Match(GhosteryMatchRequest request);
  GhosteryCosmeticResult GetCosmetics(GhosteryCosmeticRequest request);

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace focus_block

#endif  // CHROME_BROWSER_FOCUS_BLOCK_FOCUS_BLOCK_GHOSTERY_ENGINE_H_
