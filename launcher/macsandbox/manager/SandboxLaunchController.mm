// SPDX-License-Identifier: GPL-3.0-only
#import <AppKit/AppKit.h>
#include "SandboxLaunchController.h"
#include "Application.h"
#include "minecraft/MinecraftInstance.h"
#include "java/JavaUtils.h"
#include "Commandline.h"
#include <QApplication>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileDialog>
#include <QJsonArray>
#include <QJsonDocument>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QPointer>
#include <QPushButton>
#include <QSaveFile>
#include <QUuid>
#include <QVBoxLayout>
#include <QStandardPaths>
#include <QDateTime>
#include <algorithm>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <libproc.h>
#include <sys/proc_info.h>
#include <signal.h>

static QJsonObject processIdentity(qint64 pid) {
    if (pid <= 0 || pid > INT_MAX) return {};
    struct proc_bsdinfo information{};
    char path[PROC_PIDPATHINFO_MAXSIZE]{};
    if (proc_pidinfo(static_cast<int>(pid), PROC_PIDTBSDINFO, 0, &information, sizeof(information)) != sizeof(information) ||
        information.pbi_uid != getuid() || proc_pidpath(static_cast<int>(pid), path, sizeof(path)) <= 0) return {};
    return {{"pid", pid}, {"path", QString::fromUtf8(path)}, {"startSeconds", static_cast<qint64>(information.pbi_start_tvsec)},
            {"startMicroseconds", static_cast<qint64>(information.pbi_start_tvusec)}};
}

static QString activePath(const QString& identity) {
    if (identity.size() != 32 || identity.contains('/') || identity.contains('.')) return {};
    return SandboxLaunchController::managerRoot() + "/manager/active/" + identity + ".json";
}

static QJsonObject activeRecord(const QString& identity) {
    QFile file(activePath(identity));
    if (!file.open(QIODevice::ReadOnly)) return {};
    const auto record = QJsonDocument::fromJson(file.readAll()).object();
    const auto process = record["process"].toObject();
    const auto primary = record["primary"].toObject();
    const bool hostAlive = !process.isEmpty() && processIdentity(process["pid"].toInteger()) == process;
    const bool primaryAlive = !primary.isEmpty() && processIdentity(primary["pid"].toInteger()) == primary;
    if (!hostAlive && !primaryAlive) return {};
    return record;
}

static QString installedResource(const QString& name, const QString& fallback) {
    const QString bundled = QDir(QCoreApplication::applicationDirPath()).absoluteFilePath("../Resources/sandbox/" + name);
    return QFileInfo::exists(bundled) ? bundled : fallback;
}

QString SandboxLaunchController::managerRoot() { return QDir::cleanPath(QDir(APPLICATION->dataRoot()).absoluteFilePath("..")); }
QString SandboxLaunchController::backendPath() { return installedResource("manager.py", PRISM_SANDBOX_SOURCE_BACKEND); }

static QJsonObject callBackend(const QStringList& arguments, QString* error) {
    QProcess process;
    process.start("/usr/bin/python3", QStringList{"-I", "-S", SandboxLaunchController::backendPath(), "--root", SandboxLaunchController::managerRoot()} + arguments);
    if (!process.waitForFinished(15000) || process.exitCode() != 0) {
        if (error) *error = QString::fromUtf8(process.readAllStandardError());
        return {};
    }
    return QJsonDocument::fromJson(process.readAllStandardOutput()).object();
}

QJsonObject SandboxLaunchController::policy(MinecraftInstance* instance, QString* error) {
    QStringList arguments{"register", "--instance", QFileInfo(instance->instanceRoot()).absoluteFilePath(), "--name", instance->name()};
    if (isSandboxCustomInstance(instance))
        arguments << "--custom";
    auto record = callBackend(arguments, error);
    if (!record.isEmpty()) {
        const auto active = activeRecord(record["id"].toString());
        record["active"] = !active.isEmpty();
        record["activeMode"] = active["mode"].toString("game");
        const auto process = active["process"].toObject();
        record["activeStartMilliseconds"] = process["startSeconds"].toInteger() * 1000 + process["startMicroseconds"].toInteger() / 1000;
    }
    return record;
}

bool SandboxLaunchController::hasActiveRun(const QString& identity) { return !activeRecord(identity).isEmpty(); }

QString SandboxLaunchController::activeLogPath(MinecraftInstance* instance) {
    const auto value = policy(instance);
    const auto active = activeRecord(value["id"].toString());
    const QString run = active["run"].toString();
    if (active.isEmpty() || QUuid(run).isNull() || QUuid(run).toString(QUuid::WithoutBraces) != run) return {};
    return QDir(value["instanceRoot"].toString()).filePath(".prism-control/" + run + "/console.log");
}

bool SandboxLaunchController::stopExisting(MinecraftInstance* instance, QString* error) {
    const auto record = policy(instance, error);
    const auto active = activeRecord(record["id"].toString());
    if (active.isEmpty()) { if (error) *error = tr("No independently verified active helper was found."); return false; }
    auto process = active["process"].toObject();
    bool primaryOnly = process.isEmpty() || processIdentity(process["pid"].toInteger()) != process;
    if (primaryOnly) process = active["primary"].toObject();
    if (process.isEmpty() || process["pid"].toInteger() <= 0 || processIdentity(process["pid"].toInteger()) != process ||
        kill(static_cast<pid_t>(process["pid"].toInteger()), SIGTERM)) {
        if (error) *error = tr("The helper identity changed or could not be stopped."); return false;
    }
    if (primaryOnly) QTimer::singleShot(10000, QCoreApplication::instance(), [process] {
        if (processIdentity(process["pid"].toInteger()) == process) kill(static_cast<pid_t>(process["pid"].toInteger()), SIGKILL);
    });
    return true;
}

bool SandboxLaunchController::setPolicy(MinecraftInstance* instance, const QString& network, const QString& account, QString* error) {
    auto record = policy(instance, error);
    if (record.isEmpty()) return false;
    if (record["active"].toBool()) { if (error) *error = tr("Stop this instance before changing its sandbox policy."); return false; }
    QStringList args{"policy", "--id", record["id"].toString()};
    if (!network.isEmpty()) args << "--network" << network;
    if (!account.isEmpty()) args << "--account" << account;
    return !callBackend(args, error).isEmpty();
}

SandboxLaunchController::SandboxLaunchController(QObject* parent) : QObject(parent) {
    m_startTimer.setSingleShot(true);
    m_startTimer.setInterval(30000);
    connect(&m_startTimer, &QTimer::timeout, this, [this] {
        error(tr("Sandbox helper did not establish its channel within 30 seconds. If this custom data directory is in Documents, Desktop, or Downloads, complete the macOS file-access prompt or use the default Application Support location."));
    });
    m_writeTimer.setInterval(20);
    connect(&m_writeTimer, &QTimer::timeout, this, &SandboxLaunchController::flush);
    m_exitTimer.setInterval(20);
    connect(&m_exitTimer, &QTimer::timeout, this, &SandboxLaunchController::completeWhenHostExits);
    m_hostMonitor.setInterval(500);
    connect(&m_hostMonitor, &QTimer::timeout, this, [this] {
        if (m_done || m_hostIdentity.isEmpty() || processIdentity(m_hostPid) == m_hostIdentity) return;
        m_hostMonitor.stop();
        if (m_exit.isEmpty()) {
            m_exit = {{"exitCode", -1}, {"signal", 0}, {"stopped", m_stopRequested}};
            emit output({tr("The sandbox host exited without a final status. Waiting for its verified JVM to finish.")}, MessageLevel::Warning);
        }
        m_exitTimer.start();
    });
}

SandboxLaunchController::~SandboxLaunchController() {
    if (m_backend && m_backend->state() != QProcess::NotRunning) { m_backend->kill(); m_backend->waitForFinished(2000); }
    m_reader.reset();
    if (m_input >= 0) close(m_input);
    if (m_output >= 0) close(m_output);
    // Do not destroy a channel while a detached game might still be writing to it.
}

void SandboxLaunchController::error(const QString& message) {
    if (m_done) return;
    m_done = true;
    recordState(m_stopRequested ? "Cancelled" : "Failed", message);
    m_startTimer.stop(); m_writeTimer.stop(); m_exitTimer.stop(); m_hostMonitor.stop(); m_reader.reset();
    if (m_output >= 0) send("Stop");
    QString visible = message;
    for (const auto& value : m_privateValues) if (value.size() > 8) visible.replace(value, "<redacted>");
    emit output({visible}, MessageLevel::Fatal);
    emit failed(visible);
}

void SandboxLaunchController::recordState(const QString& phase, QString reason) {
    if (!m_lock || !m_lock->isLocked()) return;
    const QString identity = m_policy["id"].toString();
    if (identity.size() != 32 || identity.contains('/') || identity.contains('.')) return;
    for (const auto& value : m_privateValues) if (value.size() > 8) reason.replace(value, "<redacted>");
    const QString folder = managerRoot() + "/manager/status";
    if (!QDir().mkpath(folder)) return;
    QJsonObject value{{"version", 1}, {"instance", identity}, {"phase", phase}, {"ownerPID", QCoreApplication::applicationPid()},
        {"updatedAt", QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs)}, {"run", m_run},
        {"generation", m_report.isEmpty() ? m_policy["generation"] : m_report["manifest"].toObject()["generation"]}};
    if (!m_plan.isEmpty()) value["transaction"] = m_plan["transaction"];
    if (!reason.isEmpty()) value["error"] = QJsonObject{{"code", m_stopRequested ? "CANCELLED" : "LAUNCH_ERROR"}, {"message", reason.left(8192)}};
    QSaveFile state(folder + '/' + identity + ".json");
    if (state.open(QIODevice::WriteOnly)) {
        state.setPermissions(QFile::ReadOwner | QFile::WriteOwner);
        state.write(QJsonDocument(value).toJson(QJsonDocument::Compact)); state.commit();
    }
}

void SandboxLaunchController::backend(QStringList arguments, Completion completion) {
    m_backend = std::make_unique<QProcess>();
    connect(m_backend.get(), &QProcess::finished, this, [this, completion](int code, QProcess::ExitStatus status) {
        if (m_done) return;
        if (code != 0 || status != QProcess::NormalExit) {
            error(QString::fromUtf8(m_backend->readAllStandardError())); return;
        }
        QJsonParseError parseError;
        const auto result = QJsonDocument::fromJson(m_backend->readAllStandardOutput(), &parseError);
        if (parseError.error != QJsonParseError::NoError || !result.isObject()) {
            error(tr("Invalid response from sandbox preparation backend.")); return;
        }
        // Queue follow-up work; never destroy a QProcess while it is delivering a signal.
        const auto object = result.object();
        QTimer::singleShot(0, this, [this, completion, object] { if (!m_done) completion(object); });
    });
    connect(m_backend.get(), &QProcess::errorOccurred, this, [this](QProcess::ProcessError kind) {
        if (kind == QProcess::FailedToStart) error(tr("Could not start the local sandbox backend: %1").arg(m_backend->errorString()));
    });
    m_backend->start("/usr/bin/python3", QStringList{"-I", "-S", backendPath(), "--root", managerRoot()} + arguments);
}

void SandboxLaunchController::start(MinecraftInstance* instance, QStringList arguments, QString script, QStringList classPath) {
    m_instance = instance; m_arguments = std::move(arguments); m_script = std::move(script); m_classPath = std::move(classPath);
    QString failure;
    m_policy = policy(instance, &failure);
    if (m_policy.isEmpty()) { error(failure); return; }
    if (m_policy["active"].toBool()) { error(tr("An independently sandboxed run of this instance is still active.")); return; }
    const QString lockDir = managerRoot() + "/manager/locks";
    if (!QDir().mkpath(lockDir)) { error(tr("Cannot create manager lock directory.")); return; }
    m_lock = std::make_unique<QLockFile>(lockDir + "/" + m_policy["id"].toString() + ".lock");
    m_lock->setStaleLockTime(0);
    if (!m_lock->tryLock()) { error(tr("This instance already has an active launch.")); return; }
    recordState("Resolving");
    emit output({tr("Preparing independent App Sandbox (%1 networking).").arg(m_policy["network"].toString())}, MessageLevel::Launcher);
    for (const auto& name : {"NewLaunch.jar", "NewLaunchLegacy.jar", "JavaCheck.jar", "SandboxNativeBridge.jar"}) {
        QString path = APPLICATION->getJarPath(name);
        if (!path.isEmpty()) m_launchers << QFileInfo(path).absoluteFilePath();
    }
    m_archives = m_classPath;
    m_archives << instance->getNativeJars();
    for (const QString& folder : {instance->modsRoot(), instance->coreModsDir()}) {
        QDirIterator files(folder, {"*.jar"}, QDir::Files | QDir::NoSymLinks, QDirIterator::Subdirectories);
        while (files.hasNext()) m_archives << files.next();
    }
    if (instance->settings()->get("SandboxStandalone").toBool()) {
        QDirIterator files(QDir(instance->instanceRoot()).filePath("client"), {"*.jar"}, QDir::Files | QDir::NoSymLinks, QDirIterator::Subdirectories);
        while (files.hasNext()) m_archives << files.next();
    }
    for (auto& archive : m_archives) archive = QFileInfo(archive).absoluteFilePath();
    m_archives.removeDuplicates();
    QDirIterator nativeInputs(QDir(instance->instanceRoot()).filePath("native-inputs"), QDir::Files | QDir::NoSymLinks, QDirIterator::Subdirectories);
    while (nativeInputs.hasNext()) m_nativeInputs << nativeInputs.next();
    for (const auto& argument : m_arguments) {
        const bool javaAgent = argument.startsWith("-javaagent:");
        const bool nativeAgent = argument.startsWith("-agentpath:");
        if (!javaAgent && !nativeAgent) continue;
        const QString agent = argument.mid(argument.indexOf(':') + 1).section('=', 0, 0);
        if (!QDir::isAbsolutePath(agent)) {
            error(tr("Agent paths must be absolute paths to imported files within this instance. Import the agent, then use its full instance path.")); return;
        }
        if (javaAgent) m_archives << agent;
        else m_nativeInputs << agent;
    }
    m_archives.removeDuplicates();
    m_nativeInputs.removeDuplicates();
    for (const auto& launcher : m_launchers) m_archives.removeAll(launcher);
    // This request contains only dependency paths. Session arguments stay in memory.
    const QString requestDir = managerRoot() + "/manager/requests";
    QDir().mkpath(requestDir);
    const QString requestPath = requestDir + "/" + QUuid::createUuid().toString(QUuid::Id128) + ".json";
    QJsonArray files;
    for (const auto& file : m_archives) files.append(file);
    for (const auto& file : sandboxMavenFiles(instance)) {
        const QString absolute = QFileInfo(file).absoluteFilePath();
        if (!files.contains(absolute)) files.append(absolute);
        if (absolute.endsWith(".jar") && !m_archives.contains(absolute)) m_archives << absolute;
    }
    QSaveFile request(requestPath);
    if (!request.open(QIODevice::WriteOnly)) { error(request.errorString()); return; }
    request.setPermissions(QFile::ReadOwner | QFile::WriteOwner);
    QJsonObject preparationRequest{{"version", 1}, {"files", files}, {"assetIndex", sandboxAssetIndex(instance)}};
    if (isSandboxCustomInstance(instance)) preparationRequest["customClient"] = sandboxClientSpec(instance);
    request.write(QJsonDocument(preparationRequest).toJson(QJsonDocument::Compact));
    if (!request.commit()) { error(request.errorString()); return; }
    backend({"materialize", "--id", m_policy["id"].toString(), "--request", requestPath}, [this, requestPath](const QJsonObject& result) {
        QFile::remove(requestPath);
        m_mapping = result["mapping"].toObject();
        inspect();
    });
}

void SandboxLaunchController::startCommand(MinecraftInstance* instance, QStringList command, QString workingDirectory) {
    m_commandOnly = true;
    m_command = std::move(command);
    m_commandWorkingDirectory = std::move(workingDirectory);
    start(instance, {}, {}, {});
}

void SandboxLaunchController::inspect() {
    const QString java = QFileInfo(QStandardPaths::findExecutable(m_instance->settings()->get("JavaPath").toString())).canonicalFilePath();
    QString selected = java;
    if (selected.isEmpty() && m_commandOnly) {
        JavaUtils discovery;
        for (const auto& candidate : discovery.FindJavaPaths()) {
            const QString actual = QFileInfo(candidate).canonicalFilePath();
            if (QFileInfo::exists(QDir(QFileInfo(actual).absolutePath()).filePath("../release"))) { selected = actual; break; }
        }
    }
    if (selected.isEmpty()) { error(tr("Select a complete Java runtime before preparing this instance or command.")); return; }
    const QString home = QDir(QFileInfo(selected).absolutePath()).absoluteFilePath("..");
    QStringList args{"inspect", "--id", m_policy["id"].toString(), "--java-home", QDir::cleanPath(home),
                     "--helper", installedResource("PrismGameHost", PRISM_SANDBOX_BUILD_HOST)};
    for (const auto& archive : m_archives) args << "--archive" << archive;
    for (const auto& native : m_nativeInputs) args << "--native" << native;
    for (const auto& launcher : m_launchers) args << "--launcher" << launcher;
    backend(args, [this](const QJsonObject& plan) { review(plan); });
}

void SandboxLaunchController::review(const QJsonObject& plan) {
    m_plan = plan;
    m_planPath = plan["transaction"].toString() + "/plan.json";
    auto build = [this] {
        emit output({tr("Signing the approved native set and verifying the game bundle...")}, MessageLevel::Launcher);
        backend({"build", "--plan", m_planPath}, [this](const QJsonObject& report) { launch(report); });
    };
    if (plan["approved"].toBool()) { build(); return; }
    QDialog dialog(QApplication::activeWindow());
    dialog.setWindowTitle(tr("Review native code before preparation"));
    dialog.resize(900, 650);
    auto* layout = new QVBoxLayout(&dialog);
    auto* inventory = new QPlainTextEdit(&dialog);
    inventory->setReadOnly(true);
    inventory->setPlainText(tr("The following native files will be copied and locally signed. This is an identity and permission review, not a malware verdict.\n\n") +
                            tr("Changes since the last matching runtime approval:\n") +
                            QString::fromUtf8(QJsonDocument(plan["changes"].toObject()).toJson(QJsonDocument::Indented)) + "\n" +
                            QString::fromUtf8(QJsonDocument(plan["review"].toObject()).toJson(QJsonDocument::Indented)) +
                            "\nApproval fingerprint: " + plan["approvalFingerprint"].toString());
    layout->addWidget(inventory);
    auto* buttons = new QDialogButtonBox(QDialogButtonBox::Cancel, &dialog);
    auto* approve = buttons->addButton(tr("Approve and prepare"), QDialogButtonBox::AcceptRole);
    auto* exportButton = buttons->addButton(tr("Export inventory"), QDialogButtonBox::ActionRole);
    connect(approve, &QPushButton::clicked, &dialog, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    connect(exportButton, &QPushButton::clicked, &dialog, [&dialog, plan] {
        auto path = QFileDialog::getSaveFileName(&dialog, tr("Export native inventory"), "native-inventory.json", "JSON (*.json)");
        if (path.isEmpty()) return;
        QSaveFile file(path);
        if (file.open(QIODevice::WriteOnly)) { file.write(QJsonDocument(plan).toJson()); file.commit(); }
    });
    layout->addWidget(buttons);
    if (dialog.exec() != QDialog::Accepted) { m_stopRequested = true; error(tr("Native preparation was cancelled. No new native code was signed.")); return; }
    backend({"approve", "--plan", m_planPath, "--fingerprint", plan["approvalFingerprint"].toString()}, [build](const QJsonObject&) { build(); });
}

void SandboxLaunchController::launch(const QJsonObject& report) {
    m_report = report;
    const QString app = report["app"].toString();
    if (app.isEmpty()) { error(tr("Prepared bundle has no application path.")); return; }
    for (const auto& launcher : m_launchers)
        m_mapping[launcher] = app + "/Contents/Resources/launchers/" + QFileInfo(launcher).fileName();
    for (const auto& native : report["manifest"].toObject()["nativeFiles"].toArray()) {
        const auto record = native.toObject();
        if (!record.contains("entry") || record["entry"].toString() != QFileInfo(record["source"].toString()).fileName()) continue;
        m_mapping[record["source"].toString()] = app + "/Contents/Resources/natives/" + record["name"].toString();
    }
    const QString originalJava = m_instance->settings()->get("JavaPath").toString();
    if (originalJava.startsWith('/')) m_mapping[originalJava] = app + "/Contents/Runtime/bin/java";
    auto remap = [this](QString text) {
        auto keys = m_mapping.keys();
        std::sort(keys.begin(), keys.end(), [](const QString& a, const QString& b) { return a.size() > b.size(); });
        for (const auto& key : keys) text.replace(key, m_mapping[key].toString());
        text.replace("${sandbox_natives}", m_report["app"].toString() + "/Contents/Resources/natives");
        text.replace("${instance_directory}", m_policy["instanceRoot"].toString());
        return text;
    };
    const auto removed = report["removedNativeJars"].toArray();
    QStringList excluded;
    for (const auto& value : removed) excluded << value.toString();
    for (int i = 0; i < m_arguments.size(); ++i) {
        if (m_arguments[i] == "-cp" && i + 1 < m_arguments.size()) {
            QStringList paths = m_arguments[i + 1].split(':');
            paths.removeIf([&excluded](const QString& p) { return excluded.contains(QFileInfo(p).absoluteFilePath()); });
            for (auto& path : paths) path = remap(QFileInfo(path).absoluteFilePath());
            // LWJGL enumerates checksum resources and uses the final matching
            // non-native JAR. Keep our signed-byte checksums after class JARs.
            paths.append(app + "/Contents/Resources/native-checksums.jar");
            bool needsBridge = false;
            for (const auto& adapter : report["manifest"].toObject()["adapters"].toArray())
                needsBridge |= adapter.toObject()["id"].toString().startsWith("weblite-");
            if (needsBridge)
                paths.prepend(app + "/Contents/Resources/launchers/SandboxNativeBridge.jar");
            m_arguments[i + 1] = paths.join(':');
            ++i;
        } else m_arguments[i] = remap(m_arguments[i]);
    }
    const QString nativePath = app + "/Contents/Resources/natives";
    m_arguments.removeIf([](const QString& arg) {
        return arg.startsWith("-Djava.library.path=") || arg.startsWith("-Dorg.lwjgl.librarypath=") ||
               arg.startsWith("-Dorg.lwjgl.system.SharedLibraryExtractPath=") || arg.startsWith("-Djna.");
    });
    int insert = m_arguments.indexOf("-cp");
    if (insert < 0) insert = m_arguments.indexOf("-jar");
    if (insert < 0 && !m_commandOnly) { error(tr("Sandbox launch requires an explicit classpath or JAR entry point.")); return; }
    const QStringList properties{"-Djava.library.path=" + nativePath, "-Dorg.lwjgl.librarypath=" + nativePath,
        "-Dprism.sandbox.jcocoa=" + nativePath + "/libjcocoa.dylib",
        "-Djna.boot.library.path=" + nativePath,
        "-Djna.library.path=" + nativePath, "-Djna.nosys=true", "-Djna.nounpack=true"};
    if (!m_commandOnly) for (int i = 0; i < properties.size(); ++i) m_arguments.insert(insert + i, properties[i]);
    m_script = remap(m_script);
    if (!m_commandOnly && !m_wrapper.isEmpty()) {
        m_command = m_wrapper;
        for (auto& value : m_command) value = remap(value);
        m_command << app + "/Contents/Runtime/bin/java";
        m_command << m_arguments;
        m_arguments = m_command.mid(1);
    } else if (m_commandOnly) {
        if (m_command.isEmpty()) { error(tr("Empty sandbox command.")); return; }
        m_arguments = m_command.mid(1);
        for (auto& argument : m_arguments) argument = remap(argument);
    }
    m_run = QUuid::createUuid().toString(QUuid::WithoutBraces);
    recordState("Starting");
    // Walk each component with O_NOFOLLOW. A game cannot turn its control directory into an outside write.
    int directory = open(m_policy["instanceRoot"].toString().toUtf8().constData(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (directory < 0) { error(tr("Cannot open the instance directory safely.")); return; }
    for (const QString& part : {QString(".prism-control"), m_run}) {
        if (mkdirat(directory, part.toUtf8().constData(), 0700) != 0 && errno != EEXIST) { close(directory); error(tr("Cannot create the control directory.")); return; }
        int next = openat(directory, part.toUtf8().constData(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        close(directory); directory = next;
        if (directory < 0) { error(tr("Control directory contains a link or is inaccessible.")); return; }
    }
    if (mkfifoat(directory, "to-helper", 0600) || mkfifoat(directory, "to-manager", 0600)) {
        close(directory); error(tr("Cannot create sandbox communication FIFOs.")); return;
    }
    m_output = openat(directory, "to-helper", O_RDWR | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC);
    m_input = openat(directory, "to-manager", O_RDWR | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC);
    close(directory);
    if (m_input < 0 || m_output < 0) { error(tr("Cannot open sandbox communication FIFOs.")); return; }
    m_reader = std::make_unique<QSocketNotifier>(m_input, QSocketNotifier::Read, this);
    connect(m_reader.get(), &QSocketNotifier::activated, this, [this] { receive(); });
    m_startTimer.start();
    NSWorkspaceOpenConfiguration* config = [NSWorkspaceOpenConfiguration configuration];
    config.arguments = @[[NSString stringWithUTF8String:m_run.toUtf8().constData()]];
    config.activates = NO; config.addsToRecentItems = NO;
    config.createsNewApplicationInstance = YES; config.allowsRunningApplicationSubstitution = NO;
    config.architecture = report["manifest"].toObject()["architecture"].toString() == "x86_64" ? CPU_TYPE_X86_64 : CPU_TYPE_ARM64;
    QPointer<SandboxLaunchController> self(this);
    NSURL* url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:app.toUtf8().constData()]];
    [[NSWorkspace sharedWorkspace] openApplicationAtURL:url configuration:config completionHandler:^(NSRunningApplication* application, NSError* failure) {
        const QString message = failure ? QString::fromUtf8(failure.localizedDescription.UTF8String) : QString();
        const qint64 pid = application ? application.processIdentifier : -1;
        if (self) QMetaObject::invokeMethod(self, [self, message, pid] {
            if (!self || self->m_done) return;
            if (!message.isEmpty()) self->error(message);
            else {
                self->m_hostPid = pid;
                self->m_hostIdentity = processIdentity(pid);
                const QString expected = self->m_report["app"].toString() + "/Contents/MacOS/PrismGameHost";
                if (self->m_hostIdentity["path"].toString() != expected) { self->error(tr("LaunchServices helper process identity is invalid.")); return; }
                const QString path = activePath(self->m_policy["id"].toString());
                QDir().mkpath(QFileInfo(path).absolutePath());
                QSaveFile state(path);
                if (!state.open(QIODevice::WriteOnly)) { self->error(tr("Cannot record the active sandbox helper.")); return; }
                state.setPermissions(QFile::ReadOwner | QFile::WriteOwner);
                state.write(QJsonDocument(QJsonObject{{"version", 1}, {"process", self->m_hostIdentity}, {"run", self->m_run},
                    {"mode", self->m_commandOnly ? "command" : "game"},
                    {"generation", self->m_report["manifest"].toObject()["generation"]}}).toJson(QJsonDocument::Compact));
                if (!state.commit()) { self->error(tr("Cannot record the active sandbox helper.")); return; }
                self->m_hostMonitor.start();
                if (!self->m_pendingHello.isEmpty()) self->dispatch(self->m_pendingHello);
            }
        }, Qt::QueuedConnection);
    }];
}

void SandboxLaunchController::send(const QString& type, QJsonObject payload) {
    if (type == "Configure") {
        const QByteArray bytes = QJsonDocument(payload).toJson(QJsonDocument::Compact);
        if (bytes.size() > 4 * 1024 * 1024) { error(tr("Launch description exceeds 4 MiB.")); return; }
        if (bytes.size() > 60000) {
            send("ConfigureStart", {{"length", bytes.size()}});
            for (qsizetype offset = 0; offset < bytes.size(); offset += 16384)
                send("ConfigureChunk", {{"data", QString::fromLatin1(bytes.mid(offset, 16384).toBase64())}});
            send("ConfigureEnd");
            return;
        }
    }
    QByteArray frame = QJsonDocument(QJsonObject{{"version", 1}, {"run", m_run}, {"seq", ++m_sequence}, {"type", type}, {"payload", payload}}).toJson(QJsonDocument::Compact);
    if (frame.size() >= 65536) { error(tr("Launch description exceeds the protocol frame limit.")); return; }
    if (m_outgoing.size() + frame.size() > 8 * 1024 * 1024) { error(tr("Sandbox command queue exceeded its bound.")); return; }
    m_outgoing += frame + '\n'; flush();
}

void SandboxLaunchController::flush() {
    while (!m_outgoing.isEmpty() && m_output >= 0) {
        ssize_t written = write(m_output, m_outgoing.constData(), m_outgoing.size());
        if (written > 0) m_outgoing.remove(0, written);
        else if (errno == EINTR) continue;
        else if (errno == EAGAIN) { m_writeTimer.start(); return; }
        else { error(tr("Sandbox communication failed.")); return; }
    }
    m_writeTimer.stop();
}

void SandboxLaunchController::receive() {
    char bytes[8192]; ssize_t count;
    for (int reads = 0; reads < 8 && (count = read(m_input, bytes, sizeof(bytes))) > 0; ++reads) {
        m_incoming.append(bytes, count);
        while (true) {
            auto index = m_incoming.indexOf('\n');
            if (index < 0) break;
            if (index >= 65536) { error(tr("Oversized sandbox protocol frame.")); return; }
            const auto document = QJsonDocument::fromJson(m_incoming.left(index));
            m_incoming.remove(0, index + 1);
            const auto message = document.object();
            if (!document.isObject() || message["version"].toInt() != 1 || message["run"].toString() != m_run ||
                message["seq"].toInteger() <= m_received) { error(tr("Invalid sandbox protocol envelope.")); return; }
            m_received = message["seq"].toInteger();
            dispatch(message);
            if (m_done) return;
        }
        if (m_incoming.size() >= 65536) { error(tr("Oversized sandbox protocol frame.")); return; }
    }
}

void SandboxLaunchController::dispatch(const QJsonObject& message) {
    const auto type = message["type"].toString();
    const auto payload = message["payload"].toObject();
    if (type == "Hello") {
        if (m_hostPid < 0) { m_pendingHello = message; return; }
        const auto manifest = m_report["manifest"].toObject();
        if (payload["pid"].toInteger() != m_hostPid || payload["instance"].toString() != m_policy["id"].toString() ||
            payload["generation"] != manifest["generation"] || processIdentity(m_hostPid) != m_hostIdentity) {
            error(tr("Sandbox helper identity does not match this instance.")); return;
        }
        m_startTimer.stop();
        const QString relative = QDir(m_instance->instanceRoot()).relativeFilePath(m_commandOnly ? m_commandWorkingDirectory : m_instance->gameRoot());
        QJsonArray arguments; for (const auto& arg : m_arguments) arguments.append(arg);
        QJsonArray privateValues; for (const auto& value : m_privateValues) if (value.size() > 8) privateValues.append(value);
        QJsonObject descriptor{{"version", 1}, {"generation", manifest["generation"]}, {"arguments", arguments},
                          {"workingDirectory", QJsonObject{{"root", "instance"}, {"path", relative}}},
                          {"environment", QJsonObject{}}, {"privateValues", privateValues}, {"launchScript", m_script.isEmpty() ? QString() : m_script + "launch\n"}};
        m_expectedProgram = m_report["app"].toString() + "/" + manifest["java"].toString();
        if (!m_command.isEmpty()) {
            QString program = m_command.first();
            if (m_mapping.contains(program)) program = m_mapping[program].toString();
            if (!program.startsWith('/')) program = QStandardPaths::findExecutable(program, {"/usr/bin", "/bin"});
            if (program.isEmpty()) { error(tr("Sandbox command executable could not be found.")); return; }
            m_expectedProgram = program;
            descriptor["mode"] = "command";
            if (program.startsWith(m_report["app"].toString() + "/Contents/Resources/natives/") ||
                program == m_report["app"].toString() + "/" + manifest["java"].toString())
                descriptor["program"] = QJsonObject{{"root", "bundle"}, {"path", QDir(m_report["app"].toString()).relativeFilePath(program)}};
            else descriptor["program"] = QJsonObject{{"root", "system"}, {"path", program}};
        }
        send("Configure", descriptor);
    } else if (type == "Ready") { m_ready = true; emit ready(); if (m_proceed) proceed(); }
    else if (type == "Started") {
        recordState("Running");
        const qint64 pid = payload["pid"].toInteger();
        const auto candidate = processIdentity(pid);
        struct proc_bsdinfo information{};
        // The peer's PID is only a suggestion. Record authority only after the OS
        // confirms its parent, uid, start time and exact sealed runtime path.
        if (!m_commandOnly && m_wrapper.isEmpty() && !candidate.isEmpty() &&
            proc_pidinfo(static_cast<int>(pid), PROC_PIDTBSDINFO, 0, &information, sizeof(information)) == sizeof(information) &&
            information.pbi_ppid == m_hostPid &&
            candidate["path"].toString() == m_expectedProgram) {
            m_primaryIdentity = candidate;
            QFile current(activePath(m_policy["id"].toString()));
            if (current.open(QIODevice::ReadOnly)) {
                auto record = QJsonDocument::fromJson(current.readAll()).object(); current.close();
                if (record["run"].toString() == m_run) {
                    record["primary"] = candidate;
                    QSaveFile state(current.fileName());
                    if (state.open(QIODevice::WriteOnly)) {
                        state.setPermissions(QFile::ReadOwner | QFile::WriteOwner);
                        state.write(QJsonDocument(record).toJson(QJsonDocument::Compact)); state.commit();
                    }
                }
            }
        }
        emit started(pid); // Display only, including when a very short-lived JVM already exited.
    }
    else if (type == "Stdout" || type == "Stderr") collect(QByteArray::fromBase64(payload["data"].toString().toLatin1()), type == "Stderr");
    else if (type == "Exited") {
        collect({}, false, true); collect({}, true, true);
        m_exit = payload;
        m_reader.reset(); m_startTimer.stop(); m_writeTimer.stop();
        m_exitTimer.start();
    } else if (type == "Error") error(payload["code"].toString() + ": " + payload["message"].toString());
    else if (type != "Pong") error(tr("Unknown sandbox protocol message."));
}

void SandboxLaunchController::completeWhenHostExits() {
    if (!m_hostIdentity.isEmpty() && processIdentity(m_hostPid) == m_hostIdentity) return;
    if (!m_primaryIdentity.isEmpty() && processIdentity(m_primaryIdentity["pid"].toInteger()) == m_primaryIdentity) return;
    m_hostMonitor.stop();
    m_exitTimer.stop();
    QFile file(activePath(m_policy["id"].toString()));
    if (file.open(QIODevice::ReadOnly)) {
        const auto record = QJsonDocument::fromJson(file.readAll()).object(); file.close();
        if (record["run"].toString() == m_run) file.remove();
    }
    recordState("Exited");
    if (m_lock) m_lock->unlock();
    m_done = true;
    emit finished(m_exit["exitCode"].toInt(), m_exit["signal"].toInt(), m_exit["stopped"].toBool());
}

void SandboxLaunchController::collect(const QByteArray& bytes, bool stderrChannel, bool final) {
    QString& remainder = stderrChannel ? m_stderrRemainder : m_stdoutRemainder;
    remainder += stderrChannel ? m_stderrDecoder(bytes) : m_stdoutDecoder(bytes);
    QStringList lines;
    while (remainder.contains('\n')) { auto index = remainder.indexOf('\n'); lines << remainder.left(index).remove('\r'); remainder.remove(0, index + 1); }
    if (remainder.size() > 65536 || (final && !remainder.isEmpty())) { lines << remainder; remainder.clear(); }
    if (!lines.isEmpty()) emit output(lines, stderrChannel ? MessageLevel::StdErr : MessageLevel::StdOut);
}

void SandboxLaunchController::proceed() {
    m_proceed = true;
    if (m_ready && !m_done) { m_ready = false; send("Proceed"); }
}

void SandboxLaunchController::stop() {
    if (m_done) return;
    m_stopRequested = true;
    if (!m_hostIdentity.isEmpty() && processIdentity(m_hostPid) != m_hostIdentity && !m_primaryIdentity.isEmpty()) {
        QString reason;
        if (!stopExisting(m_instance, &reason)) error(reason);
        return;
    }
    if (m_output >= 0) send("Stop");
    else { if (m_backend) m_backend->kill(); error(tr("Sandbox preparation cancelled.")); }
}
