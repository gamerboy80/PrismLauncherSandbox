// SPDX-License-Identifier: GPL-3.0-only
#pragma once
#include <QWidget>
#include <QJsonObject>
#include "ui/pages/BasePage.h"
class MinecraftInstance;
class NewInstanceDialog;
class QComboBox;
class QLineEdit;
class QLabel;

class SandboxInstancePage : public QWidget, public BasePage {
    Q_OBJECT
public:
    explicit SandboxInstancePage(MinecraftInstance* instance);
    QString displayName() const override { return tr("Sandbox and client"); }
    QIcon icon() const override { return QIcon::fromTheme("security-high"); }
    QString id() const override { return "sandbox"; }
    bool apply() override;
    void openedImpl() override;
private:
    MinecraftInstance* m_instance;
    QComboBox *m_network, *m_account;
    QLineEdit *m_mainClass, *m_arguments;
    QString m_previousAccount;
    QLabel* m_preparation;
};

class SandboxJarPage : public QWidget, public BasePage {
    Q_OBJECT
public:
    explicit SandboxJarPage(NewInstanceDialog* dialog);
    QString displayName() const override { return tr("Import client JAR"); }
    QIcon icon() const override { return QIcon::fromTheme("java"); }
    QString id() const override { return "sandbox-jar"; }
    void openedImpl() override;
private:
    void update();
    NewInstanceDialog* m_dialog;
    QComboBox* m_kind;
    QLineEdit *m_jar, *m_version, *m_mainClass, *m_java, *m_arguments;
    QJsonObject m_metadata;
};
