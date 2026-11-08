// SPDX-License-Identifier: ISC
#import <Foundation/Foundation.h>
#include <CoreFoundation/CoreFoundation.h>
#include "event_loop.h"

void dispatchBridgeEvents()
{
    @autoreleasepool {
        // JUCE's message queue is a CFRunLoopSource on the main run loop.
        // Return immediately after handling a source. The 100ms bound is only
        // for idle signal checks; queued MIDI/SDK messages wake it directly.
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.1, true);
    }
}
