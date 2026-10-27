// SPDX-License-Identifier: GPL-3.0-only
#pragma once
#include <QObject>
#include <QJsonObject>
#include <QProcess>
#include <QTimer>
#include <QSocketNotifier>
#include <QLockFile>
#include <QStringConverter>
#include <functional>
#include <memory>
#include "MessageLevel.h"

class MinecraftInstance;
bool isSandboxCustomInstance(MinecraftInstance* instance);
QString sandboxAssetIndex(MinecraftInstance* instance);
QStringList sandboxMavenFiles(MinecraftInstance* instance);
QJsonObject sandboxClientSpec(MinecraftInstance* instance);

class SandboxLaunchController : public QObject {
    Q_OBJECT
public:
    explicit SandboxLaunchController(QObject* parent = nullptr);
    ~SandboxLaunchController() override;
    void start(MinecraftInstance* instance, QStringList arguments, QString script, QStringList classPath);
    void startCommand(MinecraftInstance* instance, QStringList command, QString workingDirectory);
    void setWrapper(QStringList wrapper) { m_wrapper = std::move(wrapper); }
    void setPrivateValues(QStringList values) { m_privateValues = std::move(values); }
    void proceed();
    void stop();
    static QString managerRoot();
    static QString backendPath();
    static QJsonObject policy(MinecraftInstance* instance, QString* error = nullptr);
    static bool setPolicy(MinecraftInstance* instance, const QString& network, const QString& account, QString* error = nullptr);
    static bool hasActiveRun(const QString& identity);
    static QString activeLogPath(MinecraftInstance* instance);
    static bool stopExisting(MinecraftInstance* instance, QString* error = nullptr);
signals:
    void ready();
    void started(qint64 pid);
    void output(QStringList lines, MessageLevel level);
    void finished(int code, int signal, bool stopped);
    void failed(QString error);
private:
    using Completion = std::function<void(const QJsonObject&)>;
    void backend(QStringList arguments, Completion completion);
    void inspect();
    void review(const QJsonObject& plan);
    void launch(const QJsonObject& report);
    void receive();
    void dispatch(const QJsonObject& message);
    void send(const QString& type, QJsonObject payload = {});
    void flush();
    void error(const QString& message);
    void collect(const QByteArray& bytes, bool stderrChannel, bool final = false);
    void completeWhenHostExits();
    void recordState(const QString& phase, QString reason = {});
    MinecraftInstance* m_instance = nullptr;
    QJsonObject m_policy, m_plan, m_report;
    QJsonObject m_hostIdentity, m_primaryIdentity, m_pendingHello, m_exit;
    QJsonObject m_mapping;
    QStringList m_arguments, m_classPath, m_archives, m_launchers;
    QStringList m_command;
    QStringList m_wrapper, m_privateValues, m_nativeInputs;
    QString m_commandWorkingDirectory;
    QString m_expectedProgram;
    bool m_commandOnly = false;
    QString m_script, m_run, m_control, m_planPath;
    QString m_stdoutRemainder, m_stderrRemainder;
    QStringDecoder m_stdoutDecoder{QStringDecoder::Utf8}, m_stderrDecoder{QStringDecoder::Utf8};
    QByteArray m_incoming, m_outgoing;
    std::unique_ptr<QProcess> m_backend;
    std::unique_ptr<QLockFile> m_lock;
    std::unique_ptr<QSocketNotifier> m_reader;
    QTimer m_startTimer, m_writeTimer, m_exitTimer, m_hostMonitor;
    int m_input = -1, m_output = -1;
    qint64 m_sequence = 0, m_received = 0, m_hostPid = -1;
    bool m_done = false, m_ready = false, m_proceed = false;
    bool m_stopRequested = false;
};
