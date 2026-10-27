// SPDX-License-Identifier: GPL-3.0-only
#import <AppKit/AppKit.h>

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc != 4) return 2;
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyProhibited];
        __block bool finished = false;
        __block int status = 1;
        NSWorkspaceOpenConfiguration *configuration = [NSWorkspaceOpenConfiguration configuration];
        configuration.arguments = @[[NSString stringWithUTF8String:argv[2]]];
        configuration.activates = NO;
        configuration.createsNewApplicationInstance = YES;
        configuration.allowsRunningApplicationSubstitution = NO;
        configuration.architecture = !strcmp(argv[3], "x86_64") ? CPU_TYPE_X86_64 : CPU_TYPE_ARM64;
        NSURL *url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]]];
        [NSWorkspace.sharedWorkspace openApplicationAtURL:url configuration:configuration completionHandler:^(NSRunningApplication *app, NSError *error) {
            NSDictionary *result = error ? @{@"error": error.localizedDescription} : @{@"pid": @(app.processIdentifier), @"executable": app.executableURL.path};
            NSData *data = [NSJSONSerialization dataWithJSONObject:result options:0 error:nil];
            fwrite(data.bytes, 1, data.length, stdout); fputc('\n', stdout);
            status = error ? 1 : 0; finished = true;
        }];
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:15];
        while (!finished && deadline.timeIntervalSinceNow > 0)
            [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.02]];
        return finished ? status : 3;
    }
}
