// SPDX-License-Identifier: ISC
#import <Foundation/Foundation.h>
#import <UserNotifications/UserNotifications.h>
#include "notifications.h"
#include <stdexcept>

static std::string text(NSString* value) { return value ? std::string([value UTF8String]) : std::string(); }
BridgeBundleConfiguration bridgeBundleConfiguration()
{
    @autoreleasepool {
        NSBundle* bundle=[NSBundle mainBundle];
        if(![[bundle bundleIdentifier] isEqualToString:@"net.xiphe.lightpad-bridge"]) return {};
        NSString* root=[bundle resourcePath];
        NSString* support=[NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/Lightpad Bridge"];
        NSString* mode=[bundle objectForInfoDictionaryKey:@"LightpadBridgeControllerMode"];
        if(mode && ![mode isEqualToString:@"legacy"] && ![mode isEqualToString:@"audio-performance"])
            throw std::runtime_error("Unknown packaged controller mode");
        bool audioPerformance=[mode isEqualToString:@"audio-performance"];
        if(audioPerformance) {
            for(NSString* name in @[@"controller.littlefoot",@"metronome.littlefoot",@"patch.littlefoot",@"status.littlefoot"])
                if(![[NSFileManager defaultManager] fileExistsAtPath:[root stringByAppendingPathComponent:name]])
                    throw std::runtime_error("Incomplete packaged Audio controller");
        }
        return {text([bundle objectForInfoDictionaryKey:@"LightpadBridgeDeviceSerial"]),
            text([root stringByAppendingPathComponent:@"controller.littlefoot"]),
            text([root stringByAppendingPathComponent:@"metronome.littlefoot"]),
            [[NSFileManager defaultManager] fileExistsAtPath:[root stringByAppendingPathComponent:@"status.littlefoot"]]
                ? text([root stringByAppendingPathComponent:@"status.littlefoot"]) : std::string(),
            text([support stringByAppendingPathComponent:@"status.json"]),
            text([support stringByAppendingPathComponent:@"bridge.lock"]),
            audioPerformance ? text([root stringByAppendingPathComponent:@"patch.littlefoot"]) : std::string(),
            audioPerformance};
    }
}
void requestBridgeNotification(const std::string& title,const std::string& body,
                               std::function<void(std::string)> completion)
{
    @autoreleasepool {
        // An unpackaged command-line executable has no notification identity.
        if(![[[NSBundle mainBundle] bundleIdentifier] isEqualToString:@"net.xiphe.lightpad-bridge"]) {
            completion("unavailable: run the packaged Lightpad Bridge app"); return;
        }
        @try {
        NSString* noticeTitle=[NSString stringWithUTF8String:title.c_str()];
        NSString* noticeBody=[NSString stringWithUTF8String:body.c_str()];
        UNUserNotificationCenter* center=[UNUserNotificationCenter currentNotificationCenter];
        [center requestAuthorizationWithOptions:UNAuthorizationOptionAlert completionHandler:^(BOOL granted,NSError* error) {
            if(!granted || error) {
                std::string result=error?"failed: "+text([error localizedDescription]):"denied: enable notifications for Lightpad Bridge in System Settings";
                dispatch_async(dispatch_get_main_queue(), ^{ completion(result); }); return;
            }
            UNMutableNotificationContent* content=[[[UNMutableNotificationContent alloc] init] autorelease];
            content.title=noticeTitle;content.body=noticeBody;
            UNNotificationRequest* request=[UNNotificationRequest requestWithIdentifier:@"lightpad-status" content:content trigger:nil];
            [center addNotificationRequest:request withCompletionHandler:^(NSError* deliveryError) {
                std::string result=deliveryError?"failed: "+text([deliveryError localizedDescription]):"requested";
                dispatch_async(dispatch_get_main_queue(), ^{ completion(result); });
            }];
        }];
        } @catch(NSException* exception) {
            completion("unavailable: " + text([exception reason]));
        }
    }
}
