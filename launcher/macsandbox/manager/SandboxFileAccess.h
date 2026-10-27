// SPDX-License-Identifier: GPL-3.0-only
#pragma once
#include <QString>
#include <QByteArray>
#include <optional>

namespace SandboxFileAccess {
bool safeRelativePath(const QString& path);
bool instancePath(const QString& path);
bool directories(const QString& path);
int openRead(const QString& path);
bool write(const QString& path, const QByteArray& bytes, bool append = false);
std::optional<QByteArray> read(const QString& path);
bool copy(const QString& source, const QString& destination, bool overwrite = false);
bool rename(const QString& source, const QString& destination);
bool remove(const QString& path, bool contentsOnly = false);
bool trash(const QString& path, QString* destination = nullptr);
}
