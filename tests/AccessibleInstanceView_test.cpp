// SPDX-License-Identifier: GPL-3.0-only
#include <QtTest>
#include <QListView>
#include <QStandardItemModel>
#include "ui/instanceview/AccessibleInstanceView_p.h"

class AccessibleInstanceViewTest : public QObject {
    Q_OBJECT
private slots:
    void nativeReleasedCellsAreRecreatedAfterModelChanges() {
        QListView view;
        QStandardItemModel model(2, 1);
        view.setModel(&model);
        struct TestAccessible : AccessibleInstanceView {
            using AccessibleInstanceView::AccessibleInstanceView;
            ~TestAccessible() override = default;
        } accessible(&view);
        auto* first = accessible.child(0);
        QVERIFY(first && first->isValid());
        QAccessible::deleteAccessibleInterface(QAccessible::uniqueId(first));
        auto* replacement = accessible.child(0);
        QVERIFY(replacement && replacement->isValid());
        QAccessible::deleteAccessibleInterface(QAccessible::uniqueId(replacement));
        model.insertRow(0);
        QAccessibleTableModelChangeEvent inserted(&view, QAccessibleTableModelChangeEvent::RowsInserted);
        accessible.modelChange(&inserted);
        QVERIFY(accessible.child(0));
        auto* second = accessible.child(1);
        QVERIFY(second);
        QAccessible::deleteAccessibleInterface(QAccessible::uniqueId(second));
        model.removeRow(1);
        QAccessibleTableModelChangeEvent removed(&view, QAccessibleTableModelChangeEvent::RowsRemoved);
        accessible.modelChange(&removed);
        QVERIFY(accessible.child(0)->isValid());
        QVERIFY(!accessible.child(-1));
        model.setColumnCount(0);
        QVERIFY(!accessible.child(0));
    }
};

QTEST_MAIN(AccessibleInstanceViewTest)
#include "AccessibleInstanceView_test.moc"
