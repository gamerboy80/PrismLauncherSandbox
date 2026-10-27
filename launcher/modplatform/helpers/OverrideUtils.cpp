#ifdef PRISM_MACOS_SANDBOX_MANAGER
#include "macsandbox/manager/SandboxFileAccess.h"
#endif
#include "OverrideUtils.h"

#include <QDirListing>

#include "FileSystem.h"

namespace Override {

void createOverrides(const QString& name, const QString& parentFolder, const QString& overridePath)
{
    QString filePath(FS::PathCombine(parentFolder, name + ".txt"));
    if (QFile::exists(filePath)) {
        FS::deletePath(filePath);
    }

    FS::ensureFilePathExists(filePath);

#ifdef PRISM_MACOS_SANDBOX_MANAGER
    QByteArray inventory;
    for (const auto& entry : QDirListing(overridePath, QDirListing::IteratorFlag::FilesOnly | QDirListing::IteratorFlag::Recursive)) {
        const QString relative = QDir(overridePath).relativeFilePath(entry.absoluteFilePath());
        if (!SandboxFileAccess::safeRelativePath(relative) || entry.isSymLink()) {
            qWarning() << "Unsafe modpack override entry" << relative; return;
        }
        inventory += relative.toUtf8() + '\n';
    }
    if (!FS::write(filePath, inventory)) qWarning() << "Cannot safely write modpack override inventory";
#else
    QFile file(filePath);
    if (!file.open(QFile::WriteOnly)) {
        qWarning() << "Failed to open file" << file.fileName() << "for writing:" << file.errorString();
        return;
    }

    for (const auto& entry : QDirListing(overridePath, QDirListing::IteratorFlag::FilesOnly | QDirListing::IteratorFlag::ResolveSymlinks |
                                                           QDirListing::IteratorFlag::Recursive)) {
        // Absolute path with temp directory -> relative path
        auto overrideFilePath = entry.absoluteFilePath().split(name).last().remove(0, 1);

        file.write(overrideFilePath.toUtf8());
        file.write("\n");
    }

    file.close();
#endif
}

QStringList readOverrides(const QString& name, const QString& parentFolder)
{
    QString filePath(FS::PathCombine(parentFolder, name + ".txt"));

#ifdef PRISM_MACOS_SANDBOX_MANAGER
    auto contents = FS::read(filePath);
    if (!contents) return {};
    QStringList result;
    for (const auto& line : QString::fromUtf8(*contents).split('\n')) {
        const QString relative = line.trimmed();
        if (relative.isEmpty()) continue;
        if (!SandboxFileAccess::safeRelativePath(relative)) {
            qWarning() << "Unsafe modpack override removal entry" << relative; return {};
        }
        result << relative;
    }
    return result;
#else
    QFile file(filePath);
    if (!file.exists()) {
        return {};
    }

    QStringList previousOverrides;

    if (!file.open(QFile::ReadOnly)) {
        qWarning() << "Failed to open file" << file.fileName() << "for reading:" << file.errorString();
        return previousOverrides;
    }

    QString entry;
    do {
        entry = file.readLine();
        previousOverrides.append(entry.trimmed());
    } while (!entry.isEmpty());

    file.close();

    return previousOverrides;
#endif
}

}  // namespace Override
