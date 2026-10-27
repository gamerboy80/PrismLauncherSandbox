// SPDX-License-Identifier: GPL-3.0-only
#import <Foundation/Foundation.h>
#include <libproc.h>
#include <sys/proc_info.h>
#include <signal.h>
#include <unistd.h>

// Acceptance-test cleanup using the same OS identity checks as the manager.
int main(int argc, char** argv) {
    @autoreleasepool {
        if (argc != 2 && argc != 3) return 2;
        if (argc == 3 && strcmp(argv[2], "--check") != 0) return 2;
        NSString *path = [NSString stringWithUTF8String:argv[1]];
        if (!path.isAbsolutePath || [path.pathComponents containsObject:@".."] || [path.pathComponents containsObject:@"."]) return 2;
        NSString *identity = path.lastPathComponent.stringByDeletingPathExtension;
        NSCharacterSet *hex = [[NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"] invertedSet];
        if (identity.length != 32 || [identity rangeOfCharacterFromSet:hex].location != NSNotFound) return 2;
        NSString *manager = path.stringByDeletingLastPathComponent.stringByDeletingLastPathComponent;
        if (![path.stringByDeletingLastPathComponent.lastPathComponent isEqual:@"active"] || ![manager.lastPathComponent isEqual:@"manager"]) return 2;
        NSDictionary *record = [NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:path] options:0 error:nil];
        NSDictionary *process = record[@"process"];
        NSString *generation = record[@"generation"];
        if (![generation isKindOfClass:NSString.class] || generation.length != 24 || [generation rangeOfCharacterFromSet:hex].location != NSNotFound) return 2;
        NSString *expected = [manager.stringByDeletingLastPathComponent stringByAppendingPathComponent:
            [NSString stringWithFormat:@"apps/%@/%@/Game.app/Contents/MacOS/PrismGameHost", identity, generation]];
        long long number = [process[@"pid"] longLongValue];
        if (number <= 0 || number > INT_MAX || ![process[@"path"] isEqual:expected]) return 3;
        pid_t pid = (pid_t)number;
        struct proc_bsdinfo information{};
        char executable[PROC_PIDPATHINFO_MAXSIZE]{};
        if (proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &information, sizeof(information)) != sizeof(information) ||
            information.pbi_uid != getuid() || proc_pidpath(pid, executable, sizeof(executable)) <= 0 ||
            ![expected isEqual:[NSString stringWithUTF8String:executable]] ||
            information.pbi_start_tvsec != [process[@"startSeconds"] unsignedLongLongValue] ||
            information.pbi_start_tvusec != [process[@"startMicroseconds"] unsignedLongLongValue]) return 3;
        if (argc == 3) return 0;
        return kill(pid, SIGTERM) ? 4 : 0;
    }
}
