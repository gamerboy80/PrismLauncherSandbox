// SPDX-License-Identifier: GPL-3.0-only
#pragma once
#include <QStringList>
#include <QProcessEnvironment>

inline bool prepareSandboxManagerStartup(const QStringList& arguments) {
    // Qt consumes these before Prism can validate arguments or its data root.
    const QStringList pluginOptions{"-platformpluginpath", "-platform", "-platformtheme", "-plugin", "-style", "-stylesheet", "-qmljsdebugger"};
    for (auto argument : arguments) {
        argument = argument.section('=', 0, 0);
        if (argument.startsWith("--")) argument.remove(0, 1);
        if (pluginOptions.contains(argument)) return false;
    }
    for (const auto& name : QProcessEnvironment::systemEnvironment().keys())
        if (name.startsWith("QT_") || name.startsWith("QML") || name.startsWith("DYLD_"))
            qunsetenv(name.toUtf8().constData());
    for (const char* name : {"CODESIGN_ALLOCATE", "DEVELOPER_DIR", "SDKROOT", "TOOLCHAINS",
                            "OPENSSL_CONF", "OPENSSL_MODULES", "OPENSSL_ENGINES", "SSL_CERT_FILE", "SSL_CERT_DIR",
                            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"})
        qunsetenv(name);
    return true;
}
