// Copyright 2026 Focus Browser contributors
// Use of this source code is governed by the license in the project root.

#ifndef CHROME_BROWSER_FOCUS_BLOCK_FOCUS_BLOCK_SERVICE_FACTORY_H_
#define CHROME_BROWSER_FOCUS_BLOCK_FOCUS_BLOCK_SERVICE_FACTORY_H_

#include "chrome/browser/profiles/profile_keyed_service_factory.h"

class Profile;

namespace base {
template <typename T>
class NoDestructor;
}

namespace focus_block {

class FocusBlockService;

class FocusBlockServiceFactory : public ProfileKeyedServiceFactory {
 public:
  static FocusBlockService* GetForProfile(Profile* profile);
  static FocusBlockServiceFactory* GetInstance();

  FocusBlockServiceFactory(const FocusBlockServiceFactory&) = delete;
  FocusBlockServiceFactory& operator=(const FocusBlockServiceFactory&) = delete;

 private:
  friend base::NoDestructor<FocusBlockServiceFactory>;

  FocusBlockServiceFactory();
  ~FocusBlockServiceFactory() override;

  std::unique_ptr<KeyedService> BuildServiceInstanceForBrowserContext(
      content::BrowserContext* context) const override;
};

}  // namespace focus_block

#endif  // CHROME_BROWSER_FOCUS_BLOCK_FOCUS_BLOCK_SERVICE_FACTORY_H_
