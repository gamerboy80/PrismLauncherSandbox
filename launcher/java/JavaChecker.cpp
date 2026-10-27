// SPDX-License-Identifier: GPL-3.0-only
/*
 *  Prism Launcher - Minecraft Launcher
 *  Copyright (C) 2022 Sefa Eyeoglu <contact@scrumplex.net>
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, version 3.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program.  If not, see <https://www.gnu.org/licenses/>.
 *
 * This file incorporates work covered by the following copyright and
 * permission notice:
 *
 *      Copyright 2013-2021 MultiMC Contributors
 *
 *      Licensed under the Apache License, Version 2.0 (the "License");
 *      you may not use this file except in compliance with the License.
 *      You may obtain a copy of the License at
 *
 *          http://www.apache.org/licenses/LICENSE-2.0
 *
 *      Unless required by applicable law or agreed to in writing, software
 *      distributed under the License is distributed on an "AS IS" BASIS,
 *      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *      See the License for the specific language governing permissions and
 *      limitations under the License.
 */

#include "JavaChecker.h"

#include <QDebug>
#include <QMap>
#include <QProcess>
#ifdef PRISM_MACOS_SANDBOX_MANAGER
#include <QFile>
#include <QFileInfo>
#include <QDir>
#include <QRegularExpression>
#endif
#include <utility>

#include "Commandline.h"
#include "FileSystem.h"
#include "java/JavaUtils.h"

JavaChecker::JavaChecker(QString path, QString args, int minMem, int maxMem, int permGen, int id)
    : m_path(std::move(path)), m_args(std::move(args)), m_minMem(minMem), m_maxMem(maxMem), m_permGen(permGen), m_id(id)
{}

void JavaChecker::executeTask()
{
#ifdef PRISM_MACOS_SANDBOX_MANAGER
    // Discovery is data-only. A real JVM check is performed later inside the prepared game sandbox.
    Result result{m_path, m_id};
    QFile release(QDir(QFileInfo(m_path).absolutePath()).absoluteFilePath("../release"));
    if (!release.open(QIODevice::ReadOnly)) {
        result.validity = Result::Validity::Errored;
        result.errorLog = tr("Cannot inspect Java's release file. Select a complete managed Java runtime.");
    } else {
        const QString text = QString::fromUtf8(release.readAll());
        auto field = [&text](const QString& name) {
            return QRegularExpression("(?:^|\\n)" + name + "=\"([^\"]*)\"").match(text).captured(1);
        };
        QString arch = field("OS_ARCH");
        if (arch.isEmpty()) {
            QFile executable(m_path);
            if (executable.open(QIODevice::ReadOnly)) {
                const QByteArray header = executable.read(8);
                if (header.size() == 8 && header.left(4) == QByteArray::fromHex("cffaedfe")) {
                    const QByteArray cpu = header.mid(4, 4);
                    if (cpu == QByteArray::fromHex("0c000001")) arch = "aarch64";
                    else if (cpu == QByteArray::fromHex("07000001")) arch = "x86_64";
                }
            }
        }
        result.javaVersion = field("JAVA_VERSION");
        result.javaVendor = field("IMPLEMENTOR");
        result.is_64bit = arch == "aarch64" || arch == "arm64" || arch == "amd64" || arch == "x86_64";
        result.mojangPlatform = result.is_64bit ? "64" : "32";
        result.realPlatform = arch;
        result.validity = !field("JAVA_VERSION").isEmpty() && result.is_64bit ? Result::Validity::Valid : Result::Validity::Errored;
        if (result.javaVendor.isEmpty()) result.javaVendor = "Managed Java";
        result.outLog = "Static runtime metadata; execution deferred to App Sandbox.";
    }
    emit checkFinished(result);
    emitSucceeded();
    return;
#endif
    QString checkerJar = JavaUtils::getJavaCheckPath();

    if (checkerJar.isEmpty()) {
        qDebug() << "Java checker library could not be found. Please check your installation.";
        return;
    }
#ifdef Q_OS_WIN
    checkerJar = FS::getPathNameInLocal8bit(checkerJar);
#endif

    QStringList args;
    m_process = std::make_unique<QProcess>();
    if (m_args.size() != 0) {
        auto extraArgs = Commandline::splitArgs(m_args);
        args.append(extraArgs);
    }
    if (m_minMem != 0) {
        args << QString("-Xms%1m").arg(m_minMem);
    }
    if (m_maxMem != 0) {
        args << QString("-Xmx%1m").arg(m_maxMem);
    }
    if (m_permGen != 64 && m_permGen != 0) {
        args << QString("-XX:PermSize=%1m").arg(m_permGen);
    }

    args.append({ "-jar", checkerJar });
    m_process->setArguments(args);
    m_process->setProgram(m_path);
    m_process->setProcessChannelMode(QProcess::SeparateChannels);
    m_process->setProcessEnvironment(CleanEnviroment());
    qDebug() << "Running java checker:" << m_path << args.join(" ");

    connect(m_process.get(), &QProcess::finished, this, &JavaChecker::finished, Qt::QueuedConnection);
    connect(m_process.get(), &QProcess::errorOccurred, this, &JavaChecker::error, Qt::QueuedConnection);
    connect(m_process.get(), &QProcess::readyReadStandardOutput, this, &JavaChecker::stdoutReady, Qt::QueuedConnection);
    connect(m_process.get(), &QProcess::readyReadStandardError, this, &JavaChecker::stderrReady, Qt::QueuedConnection);
    connect(&m_killTimer, &QTimer::timeout, this, &JavaChecker::timeout);
    m_killTimer.setSingleShot(true);
    m_killTimer.start(15000);
    m_process->start();
}

void JavaChecker::stdoutReady()
{
    QByteArray data = m_process->readAllStandardOutput();
    QString added = QString::fromLocal8Bit(data);
    added.remove('\r');
    m_stdout += added;
}

void JavaChecker::stderrReady()
{
    QByteArray data = m_process->readAllStandardError();
    QString added = QString::fromLocal8Bit(data);
    added.remove('\r');
    m_stderr += added;
}

void JavaChecker::finished(int exitcode, QProcess::ExitStatus status)
{
    m_killTimer.stop();
    m_process.reset();

    Result result = {
        m_path,
        m_id,
    };
    result.errorLog = m_stderr;
    result.outLog = m_stdout;
    qDebug() << "STDOUT" << m_stdout;
    qWarning() << "STDERR" << m_stderr;
    qDebug() << "Java checker finished with status" << status << "exit code" << exitcode;

    if (status == QProcess::CrashExit || exitcode == 1) {
        result.validity = Result::Validity::Errored;
        emit checkFinished(result);
        emitSucceeded();
        return;
    }

    bool success = true;

    QMap<QString, QString> results;

    QStringList lines = m_stdout.split("\n", Qt::SkipEmptyParts);
    for (QString line : lines) {
        line = line.trimmed();
        // NOTE: workaround for GH-4125, where garbage is getting printed into stdout on bedrock linux
        if (line.contains("/bedrock/strata")) {
            continue;
        }

        auto parts = line.split('=', Qt::SkipEmptyParts);
        if (parts.size() != 2 || parts[0].isEmpty() || parts[1].isEmpty()) {
            continue;
        }
        results.insert(parts[0], parts[1]);
    }

    if (!results.contains("os.arch") || !results.contains("java.version") || !results.contains("java.vendor") || !success) {
        result.validity = Result::Validity::ReturnedInvalidData;
        emit checkFinished(result);
        emitSucceeded();
        return;
    }

    auto osArch = results["os.arch"];
    auto javaVersion = results["java.version"];
    auto javaVendor = results["java.vendor"];
    bool is64 = osArch == "x86_64" || osArch == "amd64" || osArch == "aarch64" || osArch == "arm64" || osArch == "riscv64" ||
                osArch == "ppc64le" || osArch == "ppc64";

    result.validity = Result::Validity::Valid;
    result.is_64bit = is64;
    result.mojangPlatform = is64 ? "64" : "32";
    result.realPlatform = osArch;
    result.javaVersion = javaVersion;
    result.javaVendor = javaVendor;
    qDebug() << "Java checker succeeded.";
    emit checkFinished(result);
    emitSucceeded();
}

void JavaChecker::error(QProcess::ProcessError err)
{
    if (err == QProcess::FailedToStart) {
        qDebug() << "Java checker has failed to start:" << m_process->errorString();
        qDebug() << "Process environment:";
        qDebug() << m_process->environment();
        qDebug() << "Native environment:";
        qDebug() << QProcessEnvironment::systemEnvironment().toStringList();
        m_killTimer.stop();

        Result result = {
            .path = m_path,
            .id = m_id,
        };
        result.errorLog = m_process->errorString();
        result.validity = Result::Validity::Errored;
        emit checkFinished(result);
    }
    emitSucceeded();
}

void JavaChecker::timeout()
{
    // NO MERCY. NO ABUSE.
    if (m_process) {
        qDebug() << "Java checker has been killed by timeout.";
        m_process->kill();
    }
}
