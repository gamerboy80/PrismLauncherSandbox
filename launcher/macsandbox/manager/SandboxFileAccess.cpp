// SPDX-License-Identifier: GPL-3.0-only
#include "SandboxFileAccess.h"
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QUuid>
#include <QVariant>
#include <cstring>
#include <cerrno>
#include <fcntl.h>
#include <dirent.h>
#include <sys/stat.h>
#include <sys/clonefile.h>
#include <unistd.h>

namespace SandboxFileAccess {
bool safeRelativePath(const QString& path) {
    if (path.isEmpty() || path.contains(QChar::Null) || path.contains('\\') || QDir::isAbsolutePath(path)) return false;
    for (const auto& part : path.split('/')) if (part.isEmpty() || part == "." || part == "..") return false;
    return true;
}

static QString absolute(const QString& path) {
    QString result = QDir::cleanPath(QFileInfo(path).absoluteFilePath());
    if (result.startsWith("/var/")) result.prepend("/private");
    if (result.startsWith("/tmp/")) result.prepend("/private");
    return result;
}

bool instancePath(const QString& path) {
    auto* app = QCoreApplication::instance();
    if (!app) return false;
    const auto root = app->property("prismSandboxRoot").toString();
    return !root.isEmpty() && absolute(path).startsWith(absolute(root + "/instances") + '/');
}

static int parent(const QString& path, bool create, QByteArray& leaf) {
    if (path.contains(QChar::Null)) return -1;
    const auto parts = absolute(path).split('/', Qt::SkipEmptyParts);
    if (parts.isEmpty()) return -1;
    int fd = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    for (int i = 0; i + 1 < parts.size() && fd >= 0; ++i) {
        const auto part = parts[i].toUtf8();
        if (create && mkdirat(fd, part.constData(), 0700) && errno != EEXIST) { close(fd); return -1; }
        int next = openat(fd, part.constData(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        close(fd); fd = next;
    }
    leaf = parts.last().toUtf8();
    return fd;
}

bool directories(const QString& path) {
    QByteArray leaf;
    int fd = parent(path, true, leaf);
    if (fd < 0) return false;
    bool ok = mkdirat(fd, leaf.constData(), 0700) == 0 || errno == EEXIST;
    int directory = ok ? openat(fd, leaf.constData(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC) : -1;
    close(fd);
    if (directory < 0) return false;
    close(directory); return true;
}

bool rename(const QString& source, const QString& destination) {
    QByteArray sourceName, destinationName;
    int sourceParent = parent(source, false, sourceName);
    if (sourceParent < 0) return false;
    int destinationParent = parent(destination, true, destinationName);
    struct stat information{};
    bool safe = destinationParent >= 0 &&
        fstatat(sourceParent, sourceName.constData(), &information, AT_SYMLINK_NOFOLLOW) == 0 &&
        (S_ISREG(information.st_mode) || S_ISDIR(information.st_mode));
    bool result = safe && renameatx_np(sourceParent, sourceName.constData(), destinationParent, destinationName.constData(), RENAME_EXCL) == 0;
    close(sourceParent);
    if (destinationParent >= 0) close(destinationParent);
    return result;
}

int openRead(const QString& path) {
    QByteArray leaf;
    int directory = parent(path, false, leaf);
    if (directory < 0) return -1;
    int fd = openat(directory, leaf.constData(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    close(directory);
    if (fd < 0) return -1;
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_nlink > 1) { close(fd); return -1; }
    return fd;
}

std::optional<QByteArray> read(const QString& path) {
    int fd = openRead(path);
    if (fd < 0) return {};
    struct stat st;
    if (fstat(fd, &st) || st.st_size > 512 * 1024 * 1024) { close(fd); return {}; }
    QByteArray data;
    char buffer[65536]; ssize_t count;
    while ((count = ::read(fd, buffer, sizeof(buffer))) > 0) {
        data.append(buffer, count);
        if (data.size() > 512 * 1024 * 1024) { close(fd); return {}; }
    }
    close(fd);
    if (count < 0) return {};
    return data;
}

bool write(const QString& path, const QByteArray& bytes, bool append) {
    QByteArray content = bytes;
    if (append) {
        auto previous = read(path);
        if (previous) content.prepend(*previous);
        else if (QFileInfo::exists(path)) return false;
    }
    QByteArray leaf;
    int directory = parent(path, true, leaf);
    if (directory < 0) return false;
    const auto temp = (".prism-write-" + QUuid::createUuid().toString(QUuid::Id128)).toUtf8();
    int fd = openat(directory, temp.constData(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    bool ok = fd >= 0;
    qsizetype offset = 0;
    while (ok && offset < content.size()) {
        ssize_t count = ::write(fd, content.constData() + offset, content.size() - offset);
        if (count > 0) offset += count;
        else if (errno != EINTR) ok = false;
    }
    if (fd >= 0) { if (fsync(fd)) ok = false; close(fd); }
    if (ok) ok = renameat(directory, temp.constData(), directory, leaf.constData()) == 0;
    if (!ok) unlinkat(directory, temp.constData(), 0);
    close(directory);
    return ok;
}

bool copy(const QString& source, const QString& destination, bool overwrite) {
    QByteArray srcLeaf, dstLeaf;
    int srcDirectory = parent(source, false, srcLeaf);
    if (srcDirectory < 0) return false;
    int src = openat(srcDirectory, srcLeaf.constData(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    close(srcDirectory);
    if (src < 0) return false;
    struct stat st;
    if (fstat(src, &st) || !S_ISREG(st.st_mode) || (instancePath(source) && st.st_nlink > 1)) { close(src); return false; }
    int dstDirectory = parent(destination, true, dstLeaf);
    if (dstDirectory < 0) { close(src); return false; }
    const auto temporary = (".prism-copy-" + QUuid::createUuid().toString(QUuid::Id128)).toUtf8();
    bool ok = fclonefileat(src, dstDirectory, temporary.constData(), 0) == 0;
    if (!ok) {
        int dst = openat(dstDirectory, temporary.constData(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        ok = dst >= 0;
        char bytes[65536]; ssize_t count = 0;
        while (ok && (count = ::read(src, bytes, sizeof(bytes))) > 0) {
            ssize_t offset = 0;
            while (ok && offset < count) {
                ssize_t n = ::write(dst, bytes + offset, count - offset);
                if (n > 0) offset += n;
                else if (errno != EINTR) ok = false;
            }
        }
        if (count < 0) ok = false;
        if (dst >= 0) close(dst);
    }
    close(src);
    if (ok) {
        if (overwrite) ok = renameat(dstDirectory, temporary.constData(), dstDirectory, dstLeaf.constData()) == 0;
        else ok = linkat(dstDirectory, temporary.constData(), dstDirectory, dstLeaf.constData(), 0) == 0;
    }
    unlinkat(dstDirectory, temporary.constData(), 0);
    close(dstDirectory);
    return ok;
}

static bool removeAt(int parentFD, const char* name, bool contentsOnly) {
    struct stat st;
    if (fstatat(parentFD, name, &st, AT_SYMLINK_NOFOLLOW)) return errno == ENOENT;
    if (!S_ISDIR(st.st_mode)) return !contentsOnly && unlinkat(parentFD, name, 0) == 0;
    int fd = openat(parentFD, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return false;
    DIR* stream = fdopendir(fd);
    if (!stream) { close(fd); return false; }
    bool ok = true;
    while (auto* entry = readdir(stream)) {
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) continue;
        if (!removeAt(dirfd(stream), entry->d_name, false)) ok = false;
    }
    closedir(stream);
    if (ok && !contentsOnly) ok = unlinkat(parentFD, name, AT_REMOVEDIR) == 0;
    return ok;
}

bool remove(const QString& path, bool contentsOnly) {
    QByteArray leaf;
    int directory = parent(path, false, leaf);
    if (directory < 0) return false;
    bool ok = removeAt(directory, leaf.constData(), contentsOnly);
    close(directory); return ok;
}

bool trash(const QString& path, QString* destination) {
    if (!instancePath(path)) return false;
    const QString folder = QCoreApplication::instance()->property("prismSandboxRoot").toString() + "/manager/trash";
    if (!directories(folder)) return false;
    QByteArray leaf, ignored;
    int source = parent(path, false, leaf);
    int target = parent(folder + "/entry", false, ignored);
    if (source < 0 || target < 0) {
        if (source >= 0) close(source);
        if (target >= 0) close(target);
        return false;
    }
    const QString name = QUuid::createUuid().toString(QUuid::Id128) + "-" + QString::fromUtf8(leaf);
    const bool ok = renameat(source, leaf.constData(), target, name.toUtf8().constData()) == 0;
    close(source); close(target);
    if (ok && destination) *destination = folder + '/' + name;
    return ok;
}
}
