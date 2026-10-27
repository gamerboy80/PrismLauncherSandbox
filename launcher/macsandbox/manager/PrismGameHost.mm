// SPDX-License-Identifier: GPL-3.0-only
// This executable is the independent App Sandbox owner. Never run client code in the manager.
#import <AppKit/AppKit.h>
#import <Security/Security.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <spawn.h>
#include <cerrno>
#include <cstring>
#include <atomic>
#include <libproc.h>

static constexpr NSUInteger MaxFrame = 65536;
static int inputFD = -1, outputFD = -1;
static NSString *runID;
static NSUInteger sequence = 0;
static NSMutableData *inputBuffer;
static NSDictionary *manifest;
static NSString *instanceRoot, *bundleRoot;
static pid_t child = -1;
static int childOut = -1, childErr = -1, childInput = -1;
static NSMutableData *pendingChildInput;
static bool configured = false, started = false, stopped = false, forceKilled = false;
static NSDate *stopDeadline;
static NSDictionary *configuration;
static bool checking = false;
static NSMutableData *checkOutput;
static NSMutableData *configureBytes;
static NSUInteger configureLength = 0;

static bool groupHasOtherProcesses(pid_t leader) {
    errno = 0;
    int bytes = proc_listpids(PROC_PGRP_ONLY, static_cast<uint32_t>(leader), nullptr, 0);
    if (bytes <= 0) return errno != 0 && errno != ESRCH; // Uncertain inspection keeps supervision until the deadline.
    NSMutableData *storage = [NSMutableData dataWithLength:MIN(static_cast<NSUInteger>(bytes) + 32 * sizeof(pid_t), (NSUInteger)1048576)];
    errno = 0;
    bytes = proc_listpids(PROC_PGRP_ONLY, static_cast<uint32_t>(leader), storage.mutableBytes, static_cast<int>(storage.length));
    if (bytes <= 0) return errno != 0 && errno != ESRCH;
    auto pids = static_cast<pid_t *>(storage.mutableBytes);
    for (int i = 0; i < bytes / static_cast<int>(sizeof(pid_t)); ++i)
        if (pids[i] > 0 && pids[i] != leader) return true;
    return false;
}
static NSMutableData *outbound;
static NSMutableData *lineBuffers[2];
static bool droppingLines[2] = {false, false};
static NSArray<NSString *> *privateValues;
static int logFD = -1, controlFD = -1;
static NSUInteger loggedBytes = 0;
static volatile sig_atomic_t terminationRequested = 0;
static std::atomic_bool channelEstablished{false};
static NSString *sandboxFailure = @"Invalid sandbox entitlements";
static void requestTermination(int) { terminationRequested = 1; }

static void flushMessages() {
    while (outbound.length) {
        ssize_t n = write(outputFD, outbound.bytes, outbound.length);
        if (n > 0) [outbound replaceBytesInRange:NSMakeRange(0, (NSUInteger)n) withBytes:nullptr length:0];
        else if (errno == EINTR) continue;
        else break;
    }
}

static void sendMessage(NSString *type, NSDictionary *payload = @{}) {
    NSDictionary *frame = @{ @"version": @1, @"run": runID ?: @"", @"seq": @(++sequence), @"type": type, @"payload": payload };
    NSData *data = [NSJSONSerialization dataWithJSONObject:frame options:0 error:nil];
    if (!data || data.length >= MaxFrame) return;
    NSMutableData *line = [data mutableCopy];
    [line appendBytes:"\n" length:1];
    if (!outbound) outbound = [NSMutableData data];
    // Drop whole log frames under backpressure, never the tail of a partially written frame.
    if (outbound.length > 768 * 1024 && ([type isEqual:@"Stdout"] || [type isEqual:@"Stderr"])) return;
    if (outbound.length + line.length > 1024 * 1024) return;
    [outbound appendData:line];
    flushMessages();
}

static void fail(NSString *code, NSString *message) {
    sendMessage(@"Error", @{ @"code": code, @"message": message });
}

static void flushChildInput() {
    if (childInput < 0) return;
    if (pendingChildInput.length) {
        ssize_t written = write(childInput, pendingChildInput.bytes, MIN(pendingChildInput.length, (NSUInteger)16384));
        if (written > 0) [pendingChildInput replaceBytesInRange:NSMakeRange(0, (NSUInteger)written) withBytes:nullptr length:0];
        else if (errno != EAGAIN && errno != EINTR) [pendingChildInput setLength:0];
    }
    if (!pendingChildInput.length) { close(childInput); childInput = -1; pendingChildInput = nil; }
}

static void forwardLine(NSData *bytes, bool stderrChannel) {
    NSString *line = [[NSString alloc] initWithData:bytes encoding:NSUTF8StringEncoding];
    if (!line) line = @"[non-UTF-8 console line omitted]\n";
    for (NSString *value in privateValues) if (value.length > 8)
        line = [line stringByReplacingOccurrencesOfString:value withString:@"<redacted>"];
    NSData *safe = [line dataUsingEncoding:NSUTF8StringEncoding];
    if (logFD >= 0 && loggedBytes + safe.length <= 64 * 1024 * 1024) {
        const uint8_t *data = (const uint8_t *)safe.bytes;
        NSUInteger offset = 0;
        while (offset < safe.length) {
            ssize_t n = write(logFD, data + offset, safe.length - offset);
            if (n > 0) offset += n;
            else if (errno != EINTR) break;
        }
        loggedBytes += safe.length;
    }
    for (NSUInteger offset = 0; offset < safe.length; offset += 4096) {
        NSData *chunk = [safe subdataWithRange:NSMakeRange(offset, MIN((NSUInteger)4096, safe.length - offset))];
        sendMessage(stderrChannel ? @"Stderr" : @"Stdout", @{ @"data": [chunk base64EncodedStringWithOptions:0] });
    }
}

static void forwardOutput(NSData *data, bool stderrChannel, bool final = false) {
    int channel = stderrChannel ? 1 : 0;
    if (!lineBuffers[channel]) lineBuffers[channel] = [NSMutableData data];
    const uint8_t *bytes = (const uint8_t *)data.bytes;
    for (NSUInteger offset = 0; offset < data.length;) {
        const uint8_t *newline = (const uint8_t *)memchr(bytes + offset, '\n', data.length - offset);
        NSUInteger length = newline ? (NSUInteger)(newline - bytes) - offset + 1 : data.length - offset;
        if (!droppingLines[channel] && lineBuffers[channel].length + length <= MaxFrame)
            [lineBuffers[channel] appendBytes:bytes + offset length:length];
        else droppingLines[channel] = true;
        offset += length;
        if (newline) {
            if (droppingLines[channel]) forwardLine([@"[console line exceeded 64 KiB and was omitted]\n" dataUsingEncoding:NSUTF8StringEncoding], stderrChannel);
            else forwardLine(lineBuffers[channel], stderrChannel);
            [lineBuffers[channel] setLength:0]; droppingLines[channel] = false;
        }
    }
    if (final && lineBuffers[channel].length && !droppingLines[channel]) forwardLine(lineBuffers[channel], stderrChannel);
    if (final) [lineBuffers[channel] setLength:0];
}

static id entitlement(NSString *key) {
    SecTaskRef task = SecTaskCreateFromSelf(kCFAllocatorDefault);
    if (!task) return nil;
    id value = CFBridgingRelease(SecTaskCopyValueForEntitlement(task, (__bridge CFStringRef)key, nullptr));
    CFRelease(task);
    return value;
}

static bool verifySandbox() {
    NSLog(@"Prism sandbox startup: inspecting entitlements");
    if (![entitlement(@"com.apple.security.app-sandbox") isEqual:@YES] ||
        [entitlement(@"com.apple.security.inherit") isEqual:@YES]) return false;
    NSArray *grants = entitlement(@"com.apple.security.temporary-exception.files.absolute-path.read-write");
    if (![grants isKindOfClass:NSArray.class] || grants.count != 1 ||
        ![grants[0] isEqual:[instanceRoot stringByAppendingString:@"/"]]) return false;
    bool network = [manifest[@"network"] isEqual:@"allowed"];
    if ([entitlement(@"com.apple.security.network.client") boolValue] != network ||
        [entitlement(@"com.apple.security.network.server") boolValue] != network) return false;
    for (NSString *key in @[@"com.apple.security.application-groups", @"com.apple.security.files.user-selected.read-write",
                           @"com.apple.security.temporary-exception.files.absolute-path.read-only",
                           @"com.apple.security.cs.disable-library-validation", @"com.apple.security.get-task-allow"]) {
        if (entitlement(key)) return false;
    }
    SecCodeRef code = nullptr;
    NSLog(@"Prism sandbox startup: checking self signature");
    OSStatus status = SecCodeCopySelf(kSecCSDefaultFlags, &code);
    if (status == errSecSuccess) status = SecCodeCheckValidity(code, kSecCSStrictValidate, nullptr);
    NSLog(@"Prism sandbox startup: self signature status %d", (int)status);
    if (status == errSecSuccess) {
        CFDictionaryRef information = nullptr;
        status = SecCodeCopySigningInformation(code, kSecCSSigningInformation, &information);
        NSLog(@"Prism sandbox startup: signing information status %d", (int)status);
        if (status == errSecSuccess) {
            NSDictionary *details = CFBridgingRelease(information);
            NSDictionary *rights = details[(__bridge NSString *)kSecCodeInfoEntitlementsDict];
            NSSet *allowed = [NSSet setWithArray:@[@"com.apple.security.app-sandbox",
                @"com.apple.security.temporary-exception.files.absolute-path.read-write",
                @"com.apple.security.network.client", @"com.apple.security.network.server"]];
            for (NSString *key in rights) if (![allowed containsObject:key]) status = errSecCSReqFailed;
        }
    }
    if (status != errSecSuccess) sandboxFailure = [NSString stringWithFormat:@"Self signature/entitlement validation failed: %d", (int)status];
    if (code) CFRelease(code);
    return status == errSecSuccess;
}

// Open only a real descendant. All traversal is descriptor relative, including the control channel.
static int openBeneath(NSString *root, NSString *relative, int flags) {
    if (![relative isKindOfClass:NSString.class] || [relative hasPrefix:@"/"] || !relative.length) return -1;
    NSArray<NSString *> *parts = [relative componentsSeparatedByString:@"/"];
    int dir = open(root.fileSystemRepresentation, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dir < 0) return -1;
    for (NSUInteger i = 0; i < parts.count; ++i) {
        NSString *part = parts[i];
        if (!part.length || [part isEqual:@"."] || [part isEqual:@".."] || [part rangeOfString:@"\0"].location != NSNotFound) {
            close(dir); return -1;
        }
        int next = openat(dir, part.fileSystemRepresentation,
                          i + 1 == parts.count ? flags | O_NOFOLLOW | O_CLOEXEC : O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        close(dir);
        if (next < 0) return -1;
        dir = next;
    }
    return dir;
}

static int openFIFO(NSString *relative) {
    int fd = openBeneath(instanceRoot, relative, O_RDWR | O_NONBLOCK);
    struct stat st;
    if (fd < 0) return -1;
    if (fstat(fd, &st) || !S_ISFIFO(st.st_mode) || st.st_uid != getuid() || (st.st_mode & 0077)) {
        close(fd); return -1;
    }
    return fd;
}

static NSString *resolvePath(NSDictionary *reference, bool directory = false) {
    if (![reference isKindOfClass:NSDictionary.class]) return nil;
    NSString *base = [reference[@"root"] isEqual:@"instance"] ? instanceRoot :
                     [reference[@"root"] isEqual:@"bundle"] ? bundleRoot : nil;
    NSString *relative = reference[@"path"];
    if (!base) return nil;
    if (directory && [relative isEqual:@"."]) return base;
    int fd = openBeneath(base, relative, O_RDONLY | (directory ? O_DIRECTORY : 0));
    if (fd < 0) return nil;
    close(fd);
    return [base stringByAppendingPathComponent:relative];
}

static bool stringArray(id value) {
    if (![value isKindOfClass:NSArray.class]) return false;
    for (id item in value) if (![item isKindOfClass:NSString.class] || [item rangeOfString:@"\0"].location != NSNotFound) return false;
    return true;
}

static bool spawnJava() {
    NSString *java = [bundleRoot stringByAppendingPathComponent:manifest[@"java"]];
    if (!checking && [configuration[@"mode"] isEqual:@"command"]) {
        NSDictionary *program = configuration[@"program"];
        if (![program isKindOfClass:NSDictionary.class] || ![program[@"path"] isKindOfClass:NSString.class]) {
            fail(@"INVALID_DESCRIPTOR", @"Invalid command path reference."); return false;
        }
        if ([program[@"root"] isEqual:@"system"]) {
            NSString *path = program[@"path"];
            if (![path isKindOfClass:NSString.class] ||
                !([path.stringByDeletingLastPathComponent isEqual:@"/bin"] || [path.stringByDeletingLastPathComponent isEqual:@"/usr/bin"])) {
                fail(@"UNSUPPORTED_COMMAND", @"Only Apple system tools and prepared bundle executables can be commands."); return false;
            }
            SecStaticCodeRef code = nullptr;
            SecRequirementRef requirement = nullptr;
            OSStatus valid = SecStaticCodeCreateWithPath((__bridge CFURLRef)[NSURL fileURLWithPath:path], kSecCSDefaultFlags, &code);
            if (valid == errSecSuccess) valid = SecRequirementCreateWithString(CFSTR("anchor apple"), kSecCSDefaultFlags, &requirement);
            if (valid == errSecSuccess) valid = SecStaticCodeCheckValidity(code, kSecCSDefaultFlags, requirement);
            if (code) CFRelease(code);
            if (requirement) CFRelease(requirement);
            if (valid != errSecSuccess) { fail(@"UNSUPPORTED_COMMAND", @"System command is not Apple signed."); return false; }
            java = path;
        } else if ([program[@"root"] isEqual:@"bundle"] &&
                   ([program[@"path"] hasPrefix:@"Contents/Resources/natives/"] || [program[@"path"] isEqual:manifest[@"java"]])) {
            java = resolvePath(program);
            if (!java) { fail(@"UNSUPPORTED_COMMAND", @"Prepared command is missing."); return false; }
        } else { fail(@"UNSUPPORTED_COMMAND", @"Command executable has not been prepared."); return false; }
    }
    NSString *working = resolvePath(configuration[@"workingDirectory"], true);
    NSArray *args = checking ? @[@"-jar", [bundleRoot stringByAppendingPathComponent:@"Contents/Resources/launchers/JavaCheck.jar"]] : configuration[@"arguments"];
    if (!working || !stringArray(args)) { fail(@"INVALID_DESCRIPTOR", @"Invalid working directory or argument array."); return false; }
    // An executable chosen by a protocol message can never replace the sealed runtime.
    NSArray *argvStrings = [@[java] arrayByAddingObjectsFromArray:args];
    NSMutableDictionary *env = [NSMutableDictionary dictionary];
    for (NSString *key in @[@"LANG", @"LC_ALL", @"LC_CTYPE", @"TZ"]) {
        NSString *value = NSProcessInfo.processInfo.environment[key];
        if (value) env[key] = value;
    }
    env[@"HOME"] = NSHomeDirectory();
    env[@"TMPDIR"] = NSTemporaryDirectory();
    env[@"PATH"] = @"/usr/bin:/bin";
    env[@"JAVA_HOME"] = [[[bundleRoot stringByAppendingPathComponent:manifest[@"java"]] stringByDeletingLastPathComponent] stringByDeletingLastPathComponent];
    // Do not inherit secrets, DYLD injection, shell configuration, or Java option environment variables.
    NSDictionary *extra = configuration[@"environment"] ?: @{};
    if (![extra isKindOfClass:NSDictionary.class]) return false;
    NSSet *allowed = [NSSet setWithArray:@[@"LANG", @"LC_ALL", @"LC_CTYPE", @"TZ", @"SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS"]];
    for (NSString *key in extra) {
        if (![allowed containsObject:key] || ![extra[key] isKindOfClass:NSString.class]) {
            fail(@"INVALID_ENVIRONMENT", @"Unapproved environment variable in launch description."); return false;
        }
        env[key] = extra[key];
    }
    NSMutableArray *envStrings = [NSMutableArray array];
    for (NSString *key in env) [envStrings addObject:[NSString stringWithFormat:@"%@=%@", key, env[key]]];
    char **argv = (char **)calloc(argvStrings.count + 1, sizeof(char *));
    char **envp = (char **)calloc(envStrings.count + 1, sizeof(char *));
    for (NSUInteger i = 0; i < argvStrings.count; ++i) argv[i] = strdup([argvStrings[i] UTF8String]);
    for (NSUInteger i = 0; i < envStrings.count; ++i) envp[i] = strdup([envStrings[i] UTF8String]);
    int out[2], err[2], in[2];
    if (pipe(out) || pipe(err) || pipe(in)) return false;
    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_adddup2(&actions, in[0], STDIN_FILENO);
    posix_spawn_file_actions_adddup2(&actions, out[1], STDOUT_FILENO);
    posix_spawn_file_actions_adddup2(&actions, err[1], STDERR_FILENO);
    posix_spawn_file_actions_addchdir_np(&actions, working.fileSystemRepresentation);
    posix_spawnattr_t attributes;
    posix_spawnattr_init(&attributes);
    posix_spawnattr_setflags(&attributes, POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_CLOEXEC_DEFAULT);
    posix_spawnattr_setpgroup(&attributes, 0);
    int result = posix_spawn(&child, java.fileSystemRepresentation, &actions, &attributes, argv, envp);
    posix_spawnattr_destroy(&attributes);
    posix_spawn_file_actions_destroy(&actions);
    for (NSUInteger i = 0; i < argvStrings.count; ++i) free(argv[i]);
    for (NSUInteger i = 0; i < envStrings.count; ++i) free(envp[i]);
    free(argv); free(envp);
    close(in[0]); close(out[1]); close(err[1]);
    if (result) {
        close(in[1]); close(out[0]); close(err[0]); child = -1;
        fail(@"JAVA_EXECUTION_DENIED", [NSString stringWithFormat:@"Prepared Java could not start: %s", strerror(result)]);
        return false;
    }
    childInput = in[1]; childOut = out[0]; childErr = err[0];
    fcntl(childInput, F_SETFL, O_NONBLOCK);
    fcntl(childOut, F_SETFL, O_NONBLOCK); fcntl(childErr, F_SETFL, O_NONBLOCK);
    if (!checking) { started = true; sendMessage(@"Started", @{ @"pid": @(child) }); }
    NSString *script = checking ? nil : configuration[@"launchScript"];
    pendingChildInput = [script isKindOfClass:NSString.class] ? [[script dataUsingEncoding:NSUTF8StringEncoding] mutableCopy] : nil;
    flushChildInput();
    return true;
}

static bool consume(NSDictionary *message, NSUInteger &received) {
    if (![message isKindOfClass:NSDictionary.class] || ![message[@"version"] isKindOfClass:NSNumber.class] ||
        ![message[@"version"] isEqual:@1]) {
        fail(@"UNSUPPORTED_PROTOCOL_VERSION", @"Unsupported launch protocol version."); return false;
    }
    if (![message[@"run"] isEqual:runID] || ![message[@"seq"] isKindOfClass:NSNumber.class] ||
        [message[@"seq"] longLongValue] <= 0 || [message[@"seq"] unsignedIntegerValue] <= received ||
        ![message[@"type"] isKindOfClass:NSString.class] || ![message[@"payload"] isKindOfClass:NSDictionary.class]) {
        fail(@"INVALID_PROTOCOL", @"Invalid protocol envelope."); return false;
    }
    received = [message[@"seq"] unsignedIntegerValue];
    NSString *type = message[@"type"];
    NSDictionary *body = message[@"payload"];
    if ([type isEqual:@"ConfigureStart"] && !configured && !configureBytes) {
        configureLength = [body[@"length"] unsignedIntegerValue];
        if (!configureLength || configureLength > 4 * 1024 * 1024) return false;
        configureBytes = [NSMutableData data]; return true;
    }
    if ([type isEqual:@"ConfigureChunk"] && configureBytes && !configured) {
        NSData *chunk = [[NSData alloc] initWithBase64EncodedString:body[@"data"] options:0];
        if (!chunk || configureBytes.length + chunk.length > configureLength) return false;
        [configureBytes appendData:chunk]; return true;
    }
    if ([type isEqual:@"ConfigureEnd"] && configureBytes && !configured) {
        if (configureBytes.length != configureLength) return false;
        body = [NSJSONSerialization JSONObjectWithData:configureBytes options:0 error:nil];
        configureBytes = nil; type = @"Configure";
    }
    if ([type isEqual:@"Configure"] && !configured) {
        NSDictionary *payload = body;
        if (![payload isKindOfClass:NSDictionary.class] || ![payload[@"version"] isEqual:@1]) {
            fail(@"UNSUPPORTED_DESCRIPTOR_VERSION", @"Unsupported launch description schema."); return false;
        }
        if (![payload[@"generation"] isEqual:manifest[@"generation"]]) {
            fail(@"GENERATION_MISMATCH", @"Launch description does not match this prepared bundle."); return false;
        }
        configuration = payload; configured = true;
        privateValues = stringArray(payload[@"privateValues"]) ? payload[@"privateValues"] : @[];
        NSString *checker = [bundleRoot stringByAppendingPathComponent:@"Contents/Resources/launchers/JavaCheck.jar"];
        if ([NSFileManager.defaultManager fileExistsAtPath:checker]) {
            checking = true;
            checkOutput = [NSMutableData data];
            if (!spawnJava()) return false;
        } else sendMessage(@"Ready");
    } else if ([type isEqual:@"Proceed"] && configured && !started) {
        if (!spawnJava()) return false;
    } else if ([type isEqual:@"Stop"]) {
        stopped = true;
        if (child > 0) { kill(-child, SIGTERM); stopDeadline = [NSDate dateWithTimeIntervalSinceNow:10]; }
        else return false;
    } else if ([type isEqual:@"Ping"]) sendMessage(@"Pong");
    else { fail(@"INVALID_STATE", @"Message is not allowed in this launch state."); return false; }
    return true;
}

static void drain(int fd, NSString *type) {
    if (fd < 0) return;
    uint8_t bytes[4096];
    for (int i = 0; i < 16; ++i) {
        ssize_t n = read(fd, bytes, sizeof(bytes));
        if (n <= 0) break;
        NSData *data = [NSData dataWithBytes:bytes length:(NSUInteger)n];
        if (checking) {
            if (checkOutput.length + data.length <= MaxFrame) [checkOutput appendData:data];
            continue;
        }
        forwardOutput(data, [type isEqual:@"Stderr"]);
    }
}

int main(int argc, char **argv) {
    @autoreleasepool {
        signal(SIGPIPE, SIG_IGN);
        signal(SIGTERM, requestTermination);
        signal(SIGINT, requestTermination);
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        [NSApp finishLaunching];
        // A macOS privacy decision may block a filesystem open before a FIFO exists.
        // Never leave a hidden helper behind indefinitely; Java cannot start before this gate.
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 30 * NSEC_PER_SEC), dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
            if (!channelEstablished.load()) _exit(4);
        });
        bundleRoot = NSBundle.mainBundle.bundlePath;
        NSData *data = [NSData dataWithContentsOfFile:[bundleRoot stringByAppendingPathComponent:@"Contents/Resources/bundle-manifest.json"]];
        manifest = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        instanceRoot = manifest[@"instanceRoot"];
        if (argc != 2 || ![manifest[@"version"] isEqual:@1] || ![instanceRoot isKindOfClass:NSString.class]) {
            [NSApplication sharedApplication];
            NSAlert *alert = [NSAlert new];
            alert.messageText = @"Launch this instance from Prism Launcher";
            alert.informativeText = @"This game app is managed by the sandbox manager.";
            [alert runModal]; return 2;
        }
        runID = [NSString stringWithUTF8String:argv[1]];
        NSLog(@"Prism sandbox startup: opening control channel");
        NSUUID *uuid = [[NSUUID alloc] initWithUUIDString:runID];
        if (!uuid) return 3;
        if (!verifySandbox()) { NSLog(@"Prism sandbox verification failed: %@", sandboxFailure); return 3; }
        NSString *control = [@".prism-control" stringByAppendingPathComponent:runID];
        inputFD = openFIFO([control stringByAppendingPathComponent:@"to-helper"]);
        outputFD = openFIFO([control stringByAppendingPathComponent:@"to-manager"]);
        if (inputFD < 0 || outputFD < 0) return 4;
        NSLog(@"Prism sandbox startup: control channel open");
        channelEstablished = true;
        controlFD = openBeneath(instanceRoot, control, O_RDONLY | O_DIRECTORY);
        if (controlFD >= 0) logFD = openat(controlFD, "console.log", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        inputBuffer = [NSMutableData data];
        sendMessage(@"Hello", @{ @"generation": manifest[@"generation"], @"instance": manifest[@"instanceID"], @"pid": @(getpid()) });
        NSUInteger received = 0;
        NSDate *configureDeadline = [NSDate dateWithTimeIntervalSinceNow:30];
        bool running = true;
        while (running) {
            @autoreleasepool {
                if (terminationRequested) {
                    terminationRequested = 0; stopped = true;
                    if (child > 0) { kill(-child, SIGTERM); stopDeadline = [NSDate dateWithTimeIntervalSinceNow:10]; }
                    else { running = false; continue; }
                }
                struct pollfd p = { inputFD, POLLIN, 0 };
                flushMessages();
                flushChildInput();
                poll(&p, 1, 20);
                [NSRunLoop.currentRunLoop runMode:NSDefaultRunLoopMode beforeDate:[NSDate date]];
                uint8_t bytes[8192];
                ssize_t n;
                for (int reads = 0; reads < 8 && running && (n = read(inputFD, bytes, sizeof(bytes))) > 0; ++reads) {
                    [inputBuffer appendBytes:bytes length:(NSUInteger)n];
                    while (true) {
                        const uint8_t *start = (const uint8_t *)inputBuffer.bytes;
                        const uint8_t *end = (const uint8_t *)memchr(start, '\n', inputBuffer.length);
                        if (!end) break;
                        NSUInteger length = (NSUInteger)(end - start);
                        if (length >= MaxFrame) { running = false; break; }
                        NSData *frame = [inputBuffer subdataWithRange:NSMakeRange(0, length)];
                        [inputBuffer replaceBytesInRange:NSMakeRange(0, length + 1) withBytes:nullptr length:0];
                        NSDictionary *message = [NSJSONSerialization JSONObjectWithData:frame options:0 error:nil];
                        if (!consume(message, received)) { running = false; break; }
                    }
                    if (inputBuffer.length >= MaxFrame) { fail(@"FRAME_TOO_LARGE", @"Protocol message exceeds 64 KiB."); running = false; }
                }
                drain(childOut, @"Stdout"); drain(childErr, @"Stderr");
                if (child > 0) {
                    if (stopDeadline && stopDeadline.timeIntervalSinceNow <= 0) { kill(-child, SIGKILL); forceKilled = true; stopDeadline = nil; }
                    siginfo_t observed{};
                    if (waitid(P_PID, static_cast<id_t>(child), &observed, WEXITED | WNOHANG | WNOWAIT) == 0 && observed.si_pid == child) {
                        // Retain the zombie leader until group cleanup, so its PID/group cannot be reused.
                        kill(-child, SIGTERM);
                        if (!checking) {
                            NSDate *deadline = forceKilled ? [NSDate date] : stopDeadline ?: [NSDate dateWithTimeIntervalSinceNow:10];
                            while (groupHasOtherProcesses(child) && deadline.timeIntervalSinceNow > 0) {
                                if (terminationRequested) { terminationRequested = 0; stopped = true; }
                                drain(childOut, @"Stdout"); drain(childErr, @"Stderr"); flushMessages();
                                poll(nullptr, 0, 20);
                            }
                            if (groupHasOtherProcesses(child)) kill(-child, SIGKILL);
                        }
                        int status = 0;
                        while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
                        drain(childOut, @"Stdout"); drain(childErr, @"Stderr");
                        close(childOut); close(childErr); childOut = childErr = -1;
                        if (checking) {
                            NSString *result = [[NSString alloc] initWithData:checkOutput encoding:NSUTF8StringEncoding];
                            bool valid = WIFEXITED(status) && WEXITSTATUS(status) == 0 &&
                                         [result containsString:@"java.version="] && [result containsString:@"java.vendor="];
                            bool intel = [manifest[@"architecture"] isEqual:@"x86_64"];
                            valid = valid && (intel ? [result containsString:@"os.arch=x86_64"] || [result containsString:@"os.arch=amd64"] :
                                                      [result containsString:@"os.arch=aarch64"] || [result containsString:@"os.arch=arm64"]);
                            checking = false; child = -1;
                            if (!valid) { fail(@"JAVA_CHECK_FAILED", result ?: @"Java returned invalid runtime metadata."); running = false; }
                            else sendMessage(@"Ready");
                            continue;
                        }
                        forwardOutput([NSData data], false, true); forwardOutput([NSData data], true, true);
                        NSDictionary *summary = @{ @"version": @1, @"generation": manifest[@"generation"],
                            @"exitCode": @(WIFEXITED(status) ? WEXITSTATUS(status) : -1),
                            @"signal": @(WIFSIGNALED(status) ? WTERMSIG(status) : 0), @"stopped": @(stopped) };
                        NSData *summaryBytes = [NSJSONSerialization dataWithJSONObject:summary options:0 error:nil];
                        int summaryFD = controlFD >= 0 ? openat(controlFD, "exit.json", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600) : -1;
                        if (summaryFD >= 0) { write(summaryFD, summaryBytes.bytes, summaryBytes.length); close(summaryFD); }
                        sendMessage(@"Exited", @{ @"exitCode": @(WIFEXITED(status) ? WEXITSTATUS(status) : -1),
                                                  @"signal": @(WIFSIGNALED(status) ? WTERMSIG(status) : 0), @"stopped": @(stopped) });
                        child = -1; running = false;
                    }
                } else if (!configured && configureDeadline.timeIntervalSinceNow <= 0) running = false;
            }
        }
        if (child > 0) { kill(-child, SIGTERM); kill(-child, SIGKILL); waitpid(child, nullptr, 0); }
        if (childInput >= 0) close(childInput);
        if (logFD >= 0) close(logFD);
        if (controlFD >= 0) close(controlFD);
        NSDate *flushDeadline = [NSDate dateWithTimeIntervalSinceNow:2];
        while (outbound.length && flushDeadline.timeIntervalSinceNow > 0) {
            flushMessages(); struct pollfd p = {outputFD, POLLOUT, 0}; poll(&p, 1, 20);
        }
        return 0;
    }
}
