// SPDX-License-Identifier: GPL-3.0-only
#include "SandboxPages.h"
#include "SandboxLaunchController.h"
#include "Application.h"
#include "FileSystem.h"
#include "Commandline.h"
#include "InstanceTask.h"
#include "minecraft/MinecraftInstance.h"
#include "minecraft/PackProfile.h"
#include "settings/INISettingsObject.h"
#include "ui/dialogs/NewInstanceDialog.h"
#include <QComboBox>
#include <QFileDialog>
#include <QFormLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QProcess>
#include <QPushButton>
#include <QVBoxLayout>
#include <QCryptographicHash>

bool isSandboxCustomInstance(MinecraftInstance* instance) {
    return instance->getPackProfile()->getComponent("customjar") || instance->settings()->get("SandboxCustom").toBool() ||
           instance->settings()->get("SandboxStandalone").toBool();
}

QString sandboxAssetIndex(MinecraftInstance* instance) {
    if (instance->settings()->get("SandboxStandalone").toBool()) return {};
    auto assets = instance->getPackProfile()->getProfile()->getMinecraftAssets();
    return assets ? assets->id : QString();
}

QStringList sandboxMavenFiles(MinecraftInstance* instance) {
    QStringList result, native, native32, native64;
    for (const auto& library : instance->getPackProfile()->getProfile()->getMavenFiles()) {
        if (library->isActive(instance->runtimeContext()))
            library->getApplicableFiles(instance->runtimeContext(), result, native, native32, native64, instance->getLocalLibraryPath());
    }
    return result + native + native32 + native64;
}

QJsonObject sandboxClientSpec(MinecraftInstance* instance) {
    const bool standalone = instance->settings()->get("SandboxStandalone").toBool();
    QJsonObject result{{"version", 1}, {"launchKind", standalone ? "StandaloneJar" : "MinecraftReplacement"},
        {"jarPath", instance->settings()->get("SandboxClientJar").toString()},
        {"entryPoint", instance->settings()->get("SandboxMainClass").toString()},
        {"jvmArguments", QJsonArray::fromStringList(instance->extraArguments())},
        {"applicationArguments", QJsonArray::fromStringList(Commandline::splitArgs(instance->settings()->get("SandboxClientArguments").toString()))}};
    if (!standalone) {
        result["minecraftVersion"] = instance->getPackProfile()->getComponentVersion("net.minecraft");
        if (result["entryPoint"].toString().isEmpty()) result["entryPoint"] = instance->getMainClass();
        if (result["jarPath"].toString().isEmpty()) result["jarPath"] = "libraries/customjar-1.jar";
    }
    return result;
}

SandboxInstancePage::SandboxInstancePage(MinecraftInstance* instance) : m_instance(instance) {
    auto* form = new QFormLayout(this);
    auto* note = new QLabel(tr("The game can access only its own instance folder and macOS container. Network changes apply after stopping and relaunching it."), this);
    note->setWordWrap(true); form->addRow(note);
    m_network = new QComboBox(this);
    m_network->addItem(tr("Allowed"), "allowed"); m_network->addItem(tr("Blocked"), "blocked");
    m_account = new QComboBox(this);
    m_account->addItem(tr("Offline"), "offline"); m_account->addItem(tr("Selected Minecraft account"), "selected");
    QString failure;
    const auto policy = SandboxLaunchController::policy(instance, &failure);
    m_network->setCurrentIndex(m_network->findData(policy["network"].toString()));
    m_previousAccount = policy["account"].toString();
    m_account->setCurrentIndex(m_account->findData(m_previousAccount));
    form->addRow(tr("Game networking"), m_network); form->addRow(tr("Game account"), m_account);
    m_preparation = new QLabel(policy["preparation"].toObject()["phase"].toString("Unprepared"), this);
    form->addRow(tr("Preparation state"), m_preparation);
    m_network->setEnabled(!instance->isRunning()); m_account->setEnabled(!instance->isRunning());
    if (!failure.isEmpty()) { auto* label = new QLabel(failure, this); label->setWordWrap(true); form->addRow(label); }
    m_mainClass = new QLineEdit(instance->settings()->get("SandboxMainClass").toString(), this);
    m_arguments = new QLineEdit(instance->settings()->get("SandboxClientArguments").toString(), this);
    form->addRow(tr("Custom main class (optional)"), m_mainClass);
    form->addRow(tr("Custom client arguments"), m_arguments);
    auto* jar = new QLabel(instance->settings()->get("SandboxClientJar").toString(), this);
    jar->setTextInteractionFlags(Qt::TextSelectableByMouse);
    form->addRow(tr("Imported JAR"), jar);
    auto* nativeImport = new QPushButton(tr("Import native library or executable..."), this);
    nativeImport->setEnabled(!instance->isRunning());
    form->addRow(nativeImport);
    connect(nativeImport, &QPushButton::clicked, this, [this] {
        if (m_instance->isRunning()) return;
        const auto source = QFileDialog::getOpenFileName(this, tr("Import native input"));
        if (source.isEmpty()) return;
        const auto target = QDir(m_instance->instanceRoot()).filePath("native-inputs/" + QFileInfo(source).fileName());
        if (!FS::ensureFilePathExists(target) || !FS::copyFile(source, target)) QMessageBox::warning(this, tr("Cannot import native input"), tr("The file could not be copied safely, or a file with that name already exists."));
        else QMessageBox::information(this, tr("Native input imported"), tr("This file will be inspected and shown for approval before signing or configuration at the next launch."));
    });
    auto* libraries = new QPushButton(tr("Import additional Java libraries..."), this);
    libraries->setEnabled(!instance->isRunning());
    connect(instance, &BaseInstance::runningStatusChanged, this, [this, nativeImport, libraries](bool running) {
        m_network->setEnabled(!running); m_account->setEnabled(!running);
        nativeImport->setEnabled(!running); libraries->setEnabled(!running);
        if (running) m_preparation->setText(tr("Running"));
    });
    form->addRow(libraries);
    connect(libraries, &QPushButton::clicked, this, [this] {
        if (m_instance->isRunning()) return;
        const auto sources = QFileDialog::getOpenFileNames(this, tr("Import Java libraries"), {}, tr("Java archives (*.jar)"));
        for (const auto& source : sources) {
            const auto target = QDir(m_instance->instanceRoot()).filePath("client/libraries/" + QFileInfo(source).fileName());
            if (!FS::ensureFilePathExists(target) || !FS::copyFile(source, target)) {
                QMessageBox::warning(this, tr("Cannot import Java library"), tr("Could not safely import %1. A file with that name may already exist.").arg(source));
                return;
            }
        }
        if (!sources.isEmpty()) QMessageBox::information(this, tr("Libraries imported"),
            tr("Libraries will be inspected before launch. For a standalone client, set a main class to use the additional classpath. Manifest launches retain ordinary java -jar behavior."));
    });
}

void SandboxInstancePage::openedImpl() {
    const auto policy = SandboxLaunchController::policy(m_instance);
    m_preparation->setText(policy["active"].toBool() ? tr("Running") :
        policy["preparation"].toObject()["phase"].toString("Unprepared"));
}

bool SandboxInstancePage::apply() {
    if (m_instance->isRunning()) return true;
    const QString account = m_account->currentData().toString();
    if (account == "selected" && m_previousAccount != "selected") {
        if (QMessageBox::question(this, tr("Give this client a game session?"),
            tr("This client and its mods can read the Minecraft session token supplied for the launch. Refresh tokens and other accounts stay with the manager."),
            QMessageBox::Yes | QMessageBox::Cancel, QMessageBox::Cancel) != QMessageBox::Yes) return false;
    }
    QString failure;
    if (!SandboxLaunchController::setPolicy(m_instance, m_network->currentData().toString(), account, &failure)) {
        QMessageBox::warning(this, tr("Could not save sandbox policy"), failure); return false;
    }
    m_previousAccount = account;
    m_instance->settings()->set("SandboxMainClass", m_mainClass->text().trimmed());
    m_instance->settings()->set("SandboxClientArguments", m_arguments->text());
    return true;
}

class CustomJarCreationTask final : public InstanceTask {
public:
    CustomJarCreationTask(QJsonObject metadata, bool standalone, QString version, QString mainClass, QString java, QString arguments)
        : m_metadata(std::move(metadata)), m_standalone(standalone), m_version(std::move(version)),
          m_mainClass(std::move(mainClass)), m_java(std::move(java)), m_arguments(std::move(arguments)) {}
    void executeTask() override {
        m_instance = std::make_unique<MinecraftInstance>(m_globalSettings,
            std::make_unique<INISettingsObject>(QDir(m_stagingPath).filePath("instance.cfg")), m_stagingPath);
        m_instance->settings()->registerSetting("InstanceType", "OneSix");
        m_instance->loadSpecificSettings();
        auto* profile = m_instance->getPackProfile();
        profile->buildingFromScratch();
        m_instance->setName(name()); m_instance->setIconKey(m_instIcon);
        const QString clientDir = QDir(m_stagingPath).filePath("client");
        if (!QDir().mkpath(clientDir)) { emitFailed(tr("Cannot create client directory.")); return; }
        const QString source = m_metadata["jar"].toString();
        const QString relative = "client/" + QFileInfo(source).fileName();
        if (!FS::copyFile(source, QDir(m_stagingPath).filePath(relative))) { emitFailed(tr("Cannot copy the client JAR.")); return; }
        auto copied = FS::read(QDir(m_stagingPath).filePath(relative));
        if (!copied || QString::fromLatin1(QCryptographicHash::hash(*copied, QCryptographicHash::Sha256).toHex()) != m_metadata["sha256"].toString()) {
            emitFailed(tr("Client JAR changed after inspection. Select it again.")); return;
        }
        for (const auto& item : m_metadata["dependencies"].toArray()) {
            const auto dependency = item.toObject();
            const QString target = QDir(clientDir).filePath(dependency["relative"].toString());
            if (!FS::ensureFilePathExists(target) || !FS::copyFile(dependency["source"].toString(), target)) {
                emitFailed(tr("Cannot import a manifest dependency: %1").arg(target)); return;
            }
            auto content = FS::read(target);
            if (!content || QString::fromLatin1(QCryptographicHash::hash(*content, QCryptographicHash::Sha256).toHex()) != dependency["sha256"].toString()) {
                emitFailed(tr("A manifest dependency changed after inspection.")); return;
            }
        }
        m_instance->settings()->set("SandboxStandalone", m_standalone);
        m_instance->settings()->set("SandboxCustom", true);
        m_instance->settings()->set("SandboxClientJar", relative);
        m_instance->settings()->set("SandboxMainClass", m_mainClass);
        m_instance->settings()->set("SandboxClientArguments", m_arguments);
        if (!m_java.isEmpty()) {
            m_instance->settings()->set("OverrideJavaLocation", true);
            m_instance->settings()->set("JavaPath", m_java);
        }
        if (!m_standalone) {
            profile->setComponentVersion("net.minecraft", m_version, true);
            profile->installCustomJar(QDir(m_stagingPath).filePath(relative));
        }
        profile->saveNow();
        if (m_standalone) emitSucceeded();
        else downloadFiles(m_instance.get());
    }
private:
    QJsonObject m_metadata;
    bool m_standalone;
    QString m_version, m_mainClass, m_java, m_arguments;
    std::unique_ptr<MinecraftInstance> m_instance;
};

SandboxJarPage::SandboxJarPage(NewInstanceDialog* dialog) : m_dialog(dialog) {
    auto* layout = new QFormLayout(this);
    m_kind = new QComboBox(this);
    m_kind->addItem(tr("Minecraft replacement"), false); m_kind->addItem(tr("Standalone runnable JAR"), true);
    m_jar = new QLineEdit(this); m_jar->setReadOnly(true);
    auto* browse = new QPushButton(tr("Choose client JAR..."), this);
    auto* row = new QHBoxLayout; row->addWidget(m_jar); row->addWidget(browse);
    m_version = new QLineEdit(this); m_version->setPlaceholderText("e.g. 1.20.1");
    m_mainClass = new QLineEdit(this); m_mainClass->setPlaceholderText(tr("Base Minecraft entry point, or JAR manifest Main-Class"));
    m_java = new QLineEdit(APPLICATION->settings()->get("JavaPath").toString(), this);
    auto* javaBrowse = new QPushButton(tr("Choose Java executable..."), this);
    auto* javaRow = new QHBoxLayout; javaRow->addWidget(m_java); javaRow->addWidget(javaBrowse);
    m_arguments = new QLineEdit(this);
    layout->addRow(tr("Launch kind"), m_kind); layout->addRow(tr("Client JAR"), row);
    layout->addRow(tr("Minecraft base version"), m_version); layout->addRow(tr("Main class override"), m_mainClass);
    layout->addRow(tr("Java (blank uses automatic selection)"), javaRow); layout->addRow(tr("Client arguments"), m_arguments);
    auto* note = new QLabel(tr("Imported clients start offline, with networking allowed. Native code is reviewed separately before signing. Choose an Intel Java runtime for clients requiring Intel-only natives."), this);
    note->setWordWrap(true); layout->addRow(note);
    connect(browse, &QPushButton::clicked, this, [this] {
        const QString jar = QFileDialog::getOpenFileName(this, tr("Import custom client JAR"), {}, "Java archives (*.jar)");
        if (jar.isEmpty()) return;
        QProcess inspect;
        inspect.start("/usr/bin/python3", {"-I", "-S", SandboxLaunchController::backendPath(), "--root", SandboxLaunchController::managerRoot(), "inspect-client", "--jar", jar});
        if (!inspect.waitForFinished(15000) || inspect.exitCode() != 0) {
            QMessageBox::warning(this, tr("Cannot import client"), QString::fromUtf8(inspect.readAllStandardError())); return;
        }
        m_metadata = QJsonDocument::fromJson(inspect.readAllStandardOutput()).object();
        m_jar->setText(jar); update();
    });
    connect(javaBrowse, &QPushButton::clicked, this, [this] {
        const QString path = QFileDialog::getOpenFileName(this, tr("Select Java executable"), "/Library/Java/JavaVirtualMachines");
        if (!path.isEmpty()) m_java->setText(path);
    });
    connect(m_kind, &QComboBox::currentIndexChanged, this, [this] { m_version->setEnabled(!m_kind->currentData().toBool()); update(); });
    for (auto* edit : {m_version, m_mainClass, m_java, m_arguments}) connect(edit, &QLineEdit::textChanged, this, &SandboxJarPage::update);
}

void SandboxJarPage::openedImpl() { update(); }

void SandboxJarPage::update() {
    const bool standalone = m_kind->currentData().toBool();
    const bool valid = !m_metadata.isEmpty() && (standalone ? !m_metadata["mainClass"].toString().isEmpty() || !m_mainClass->text().isEmpty() : !m_version->text().trimmed().isEmpty());
    if (!valid) { m_dialog->setSuggestedPack({}, nullptr); return; }
    auto* task = new CustomJarCreationTask(m_metadata, standalone, m_version->text().trimmed(), m_mainClass->text().trimmed(), m_java->text().trimmed(), m_arguments->text());
    m_dialog->setSuggestedPack(QFileInfo(m_jar->text()).completeBaseName(), task);
}
