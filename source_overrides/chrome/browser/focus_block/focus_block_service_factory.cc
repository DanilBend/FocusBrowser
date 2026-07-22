// Copyright 2026 Focus Browser contributors
// Use of this source code is governed by the license in the project root.

#include "chrome/browser/focus_block/focus_block_service_factory.h"

#include "base/no_destructor.h"
#include "chrome/browser/focus_block/focus_block_service.h"
#include "chrome/browser/profiles/profile.h"

namespace focus_block {

// static
FocusBlockService* FocusBlockServiceFactory::GetForProfile(Profile* profile) {
  return static_cast<FocusBlockService*>(
      GetInstance()->GetServiceForBrowserContext(profile, /*create=*/true));
}

// static
FocusBlockServiceFactory* FocusBlockServiceFactory::GetInstance() {
  static base::NoDestructor<FocusBlockServiceFactory> instance;
  return instance.get();
}

FocusBlockServiceFactory::FocusBlockServiceFactory()
    : ProfileKeyedServiceFactory(
          "FocusBlockService",
          ProfileSelections::Builder()
              .WithRegular(ProfileSelection::kOwnInstance)
              .WithGuest(ProfileSelection::kOwnInstance)
              .Build()) {}

FocusBlockServiceFactory::~FocusBlockServiceFactory() = default;

std::unique_ptr<KeyedService>
FocusBlockServiceFactory::BuildServiceInstanceForBrowserContext(
    content::BrowserContext* context) const {
  return std::make_unique<FocusBlockService>(
      Profile::FromBrowserContext(context));
}

}  // namespace focus_block
