// SPDX-License-Identifier: ISC
#pragma once
#include <functional>
#include <string>
struct BridgeBundleConfiguration {
    std::string serial,source,modalSource,statusSource,status,lock;
};
BridgeBundleConfiguration bridgeBundleConfiguration();
// Called only after an explicit status tap or user-requested permission action.
// Completion executes on the main queue. No sounds, badge or remote service.
void requestBridgeNotification(const std::string& title,const std::string& body,
                               std::function<void(std::string)> completion);
