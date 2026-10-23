#import "GeneratedPluginRegistrant.h"

#if DEBUG
#import <Foundation/Foundation.h>
#import <TargetConditionals.h>
#if TARGET_OS_SIMULATOR
// Simulator debug builds only (DebugHealthSeeder in AppDelegate.swift): runs `block` and returns the Objective-C exception
// it raised, nil if none. HealthKit raises instead of returning an error when asked to share a type no app may write
// (Apple-computed types such as exercise minutes), and Swift cannot catch that.
static inline NSException *_Nullable W2WCatchException(void (NS_NOESCAPE ^_Nonnull block)(void)) {
  @try {
    block();
  } @catch (NSException *e) {
    return e;
  }
  return nil;
}
#endif
#endif
