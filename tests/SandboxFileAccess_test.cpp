// SPDX-License-Identifier: GPL-3.0-only
#include <QtTest>
#include <QTemporaryDir>
#include <QFile>
#include <QDir>
#include <atomic>
#include <thread>
#include <unistd.h>
#include "FileSystem.h"
#include "settings/INIFile.h"
#include "macsandbox/manager/SandboxFileAccess.h"
#include "macsandbox/manager/SandboxStartup.h"
#include "PSaveFile.h"
#include "minecraft/AssetsUtils.h"
#include "modplatform/helpers/OverrideUtils.h"
#include "modplatform/packwiz/Packwiz.h"
#include <QJsonDocument>
#include <QJsonObject>

class SandboxFileAccessTest : public QObject {
    Q_OBJECT
private slots:
    void metadataPathsCannotEscapeTheirInstance() {
        QTemporaryDir temporary;
        const QString root = temporary.path();
        QCoreApplication::instance()->setProperty("prismSandboxRoot", root);
        const QString instance = root + "/instances/one";
        QVERIFY(SandboxFileAccess::directories(instance));
        const QString index = instance + "/assets.json";
        auto writeIndex = [&](const QString& path, const QString& hash) {
            return FS::write(index, QJsonDocument(QJsonObject{{"objects", QJsonObject{{path, QJsonObject{{"hash", hash}, {"size", 1}}}}}}).toJson()).has_value();
        };
        AssetsIndex assets;
        QVERIFY(writeIndex("textures/example.png", QString(40, 'a')));
        QVERIFY(AssetsUtils::loadAssetsIndexJson("authored", index, assets));
        QCOMPARE(assets.id, QString("authored"));
        for (const QString path : {"../manager/secret", "/absolute", "a/../../escape", "a\\escape"}) {
            QVERIFY(writeIndex(path, QString(40, 'a')));
            QVERIFY(!AssetsUtils::loadAssetsIndexJson("authored", index, assets));
        }
        QVERIFY(writeIndex("safe", "../../manager/secret"));
        QVERIFY(!AssetsUtils::loadAssetsIndexJson("authored", index, assets));
        QVERIFY(FS::write(instance + "/overrides.txt", "config/safe.txt\n../manager/secret\n"));
        QVERIFY(Override::readOverrides("overrides", instance).isEmpty());
        QVERIFY(FS::write(root + "/secret.pw.toml", "keep"));
        QString unsafeSlug = "../../secret";
        Packwiz::V1::deleteModIndex(QDir(instance), unsafeSlug);
        QCOMPARE(*FS::read(root + "/secret.pw.toml"), QByteArray("keep"));
        QVERIFY(!Packwiz::V1::getIndexForMod(QDir(instance), unsafeSlug).isValid());
        QCoreApplication::instance()->setProperty("prismSandboxRoot", {});
    }
    void managedRenameRejectsLinkedParentsAndPreservesExistingFiles() {
        QTemporaryDir temporary;
        const QString root = temporary.path();
        QCoreApplication::instance()->setProperty("prismSandboxRoot", root);
        QVERIFY(SandboxFileAccess::directories(root + "/instances/one/mods"));
        QVERIFY(SandboxFileAccess::directories(root + "/outside"));
        QVERIFY(SandboxFileAccess::write(root + "/outside/secret", "keep"));
        QVERIFY(::symlink(qPrintable(root + "/outside"), qPrintable(root + "/instances/one/link")) == 0);
        QVERIFY(!FS::move(root + "/instances/one/link/secret", root + "/instances/one/stolen"));
        QVERIFY(SandboxFileAccess::write(root + "/instances/one/mods/client.jar", "client"));
        QVERIFY(FS::move(root + "/instances/one/mods/client.jar", root + "/instances/one/mods/client.jar.disabled"));
        QVERIFY(SandboxFileAccess::write(root + "/instances/one/mods/client.jar", "replacement"));
        QVERIFY(!SandboxFileAccess::rename(root + "/instances/one/mods/client.jar", root + "/instances/one/mods/client.jar.disabled"));
        QCOMPARE(*SandboxFileAccess::read(root + "/instances/one/mods/client.jar.disabled"), QByteArray("client"));
        QCOMPARE(*SandboxFileAccess::read(root + "/outside/secret"), QByteArray("keep"));
        QCoreApplication::instance()->setProperty("prismSandboxRoot", {});
    }
    void startupCannotLoadGameControlledQtPlugins() {
        for (const QString option : {"-plugin", "--plugin=/game/plugin", "-platformpluginpath", "--style", "-platformtheme"})
            QVERIFY(!prepareSandboxManagerStartup({"--dir", "/manager", option, "/game/plugin"}));
        qputenv("QT_PLUGIN_PATH", "/game/plugins");
        qputenv("QML_IMPORT_PATH", "/game/qml");
        qputenv("DYLD_LIBRARY_PATH", "/game/natives");
        qputenv("CODESIGN_ALLOCATE", "/game/signing-tool");
        qputenv("OPENSSL_CONF", "/game/openssl.cnf");
        QVERIFY(prepareSandboxManagerStartup({"--dir", "/manager", "--launch", "instance"}));
        QVERIFY(!qEnvironmentVariableIsSet("QT_PLUGIN_PATH"));
        QVERIFY(!qEnvironmentVariableIsSet("QML_IMPORT_PATH"));
        QVERIFY(!qEnvironmentVariableIsSet("DYLD_LIBRARY_PATH"));
        QVERIFY(!qEnvironmentVariableIsSet("CODESIGN_ALLOCATE"));
        QVERIFY(!qEnvironmentVariableIsSet("OPENSSL_CONF"));
    }
    void managerTrashMovesEntriesWithoutFollowingParents() {
        QTemporaryDir temporary;
        const QString root = temporary.path();
        QCoreApplication::instance()->setProperty("prismSandboxRoot", root);
        QVERIFY(QDir().mkpath(root + "/instances/one"));
        QVERIFY(QDir().mkpath(root + "/outside"));
        QVERIFY(SandboxFileAccess::write(root + "/outside/secret", "keep"));
        QVERIFY(::symlink(qPrintable(root + "/outside"), qPrintable(root + "/instances/one/link")) == 0);
        QVERIFY(!FS::trash(root + "/instances/one/link/secret"));
        QVERIFY(SandboxFileAccess::write(root + "/instances/one/local", "recoverable"));
        QString destination;
        QVERIFY(FS::trash(root + "/instances/one/local", &destination));
        QVERIFY(destination.startsWith(root + "/manager/trash/"));
        QCOMPARE(*SandboxFileAccess::read(destination), QByteArray("recoverable"));
        QCOMPARE(*SandboxFileAccess::read(root + "/outside/secret"), QByteArray("keep"));
        QCoreApplication::instance()->setProperty("prismSandboxRoot", {});
    }
    void downloadCommitRejectsReplacedParentDirectory() {
        QTemporaryDir temporary;
        const QString root = temporary.path();
        QCoreApplication::instance()->setProperty("prismSandboxRoot", root);
        QVERIFY(QDir().mkpath(root + "/instances/one/mods"));
        QVERIFY(QDir().mkpath(root + "/outside"));
        QVERIFY(SandboxFileAccess::write(root + "/outside/client.jar", "keep"));
        PSaveFile download(root + "/instances/one/mods/client.jar");
        QVERIFY(download.open(QIODevice::WriteOnly)); QCOMPARE(download.write("downloaded"), qint64(10));
        QVERIFY(QDir(root + "/instances/one/mods").removeRecursively());
        QVERIFY(::symlink(qPrintable(root + "/outside"), qPrintable(root + "/instances/one/mods")) == 0);
        QVERIFY(!download.commit());
        QCOMPARE(*SandboxFileAccess::read(root + "/outside/client.jar"), QByteArray("keep"));
        QCoreApplication::instance()->setProperty("prismSandboxRoot", {});
    }
    void noSymlinkTraversalForManagerWrites() {
        QTemporaryDir temporary;
        const QString root = temporary.path();
        QCoreApplication::instance()->setProperty("prismSandboxRoot", root);
        QVERIFY(QDir().mkpath(root + "/instances/one"));
        QVERIFY(QDir().mkpath(root + "/outside"));
        QFile original(root + "/outside/secret");
        QVERIFY(original.open(QIODevice::WriteOnly)); original.write("unchanged"); original.close();
        QVERIFY(::symlink(qPrintable(root + "/outside"), qPrintable(root + "/instances/one/jump")) == 0);
        QVERIFY(!FS::write(root + "/instances/one/jump/secret", "bad"));
        QVERIFY(!FS::read(root + "/instances/one/jump/secret"));
        QVERIFY(!FS::copyFile(root + "/outside/secret", root + "/instances/one/jump/copied"));
        INIFile config; config.set("name", "bad");
        QVERIFY(!config.saveFile(root + "/instances/one/jump/secret"));
        QVERIFY(!SandboxFileAccess::remove(root + "/instances/one/jump/secret"));
        QVERIFY(original.open(QIODevice::ReadOnly)); QCOMPARE(original.readAll(), QByteArray("unchanged"));
        QCoreApplication::instance()->setProperty("prismSandboxRoot", {});
    }
    void replacingLeafLinkNeverChangesItsTarget() {
        QTemporaryDir temporary;
        const QString root = temporary.path();
        QVERIFY(QDir().mkpath(root + "/instance"));
        QFile source(root + "/secret"); QVERIFY(source.open(QIODevice::WriteOnly)); source.write("secret"); source.close();
        QVERIFY(::symlink(qPrintable(root + "/secret"), qPrintable(root + "/instance/link")) == 0);
        QVERIFY(SandboxFileAccess::write(root + "/instance/link", "local"));
        QVERIFY(source.open(QIODevice::ReadOnly)); QCOMPARE(source.readAll(), QByteArray("secret"));
        QCOMPARE(*SandboxFileAccess::read(root + "/instance/link"), QByteArray("local"));
    }
    void deletingTreeDoesNotFollowContainedLinks() {
        QTemporaryDir temporary;
        const QString root = temporary.path();
        QVERIFY(QDir().mkpath(root + "/instance/sub")); QVERIFY(QDir().mkpath(root + "/outside"));
        QVERIFY(SandboxFileAccess::write(root + "/outside/secret", "keep"));
        QVERIFY(::symlink(qPrintable(root + "/outside"), qPrintable(root + "/instance/sub/link")) == 0);
        QVERIFY(SandboxFileAccess::remove(root + "/instance"));
        QCOMPARE(*SandboxFileAccess::read(root + "/outside/secret"), QByteArray("keep"));
    }
    void concurrentParentReplacementCannotRedirectWrites() {
        QTemporaryDir temporary;
        const QString root = temporary.path();
        const QString stable = root + "/instance/target", moved = root + "/instance/moved", outside = root + "/outside";
        QVERIFY(QDir().mkpath(stable)); QVERIFY(QDir().mkpath(outside));
        QVERIFY(SandboxFileAccess::write(outside + "/sentinel", "keep"));
        std::atomic<bool> finish{false};
        std::thread attacker([&] {
            while (!finish) {
                if (::rename(qPrintable(stable), qPrintable(moved)) == 0) {
                    ::symlink(qPrintable(outside), qPrintable(stable));
                    ::unlink(qPrintable(stable));
                    ::rename(qPrintable(moved), qPrintable(stable));
                }
            }
        });
        for (int i = 0; i < 1000; ++i) SandboxFileAccess::write(stable + "/sentinel", "local");
        finish = true; attacker.join();
        QCOMPARE(*SandboxFileAccess::read(outside + "/sentinel"), QByteArray("keep"));
    }
};
QTEST_GUILESS_MAIN(SandboxFileAccessTest)
#include "SandboxFileAccess_test.moc"
