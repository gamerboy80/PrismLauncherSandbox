// SPDX-License-Identifier: GPL-3.0-only
// Regression for the ownership contract in Qt change 765434 / QTBUG-149612.
#import <AppKit/AppKit.h>
#include <QtTest>
#include <QTableWidget>
#include <QAccessible>

@interface NSObject (PrismAccessibilityTest)
+ (id)elementWithInterface:(QAccessibleInterface*)interface;
- (NSArray*)accessibilityChildren;
- (NSArray*)accessibilitySelectedChildren;
@end

class QtCocoaAccessibilityTest : public QObject {
    Q_OBJECT
private slots:
    void selectedCellsKeepTheirParentTableAlive() {
        @autoreleasepool {
            QTableWidget table(3, 2);
            table.setSelectionBehavior(QAbstractItemView::SelectRows);
            for (int r = 0; r < 3; ++r)
                for (int c = 0; c < 2; ++c) table.setItem(r, c, new QTableWidgetItem(QString::number(r * 2 + c)));
            QAccessible::setActive(true);
            auto* interface = QAccessible::queryAccessibleInterface(&table);
            QVERIFY(interface);
            const auto identity = QAccessible::uniqueId(interface);
            Class cocoaClass = NSClassFromString(@"QMacAccessibilityElement");
            QVERIFY(cocoaClass);
            id element = [[cocoaClass elementWithInterface:interface] retain];
            QVERIFY(element);
            for (id child in [element accessibilityChildren]) [child accessibilityChildren];
            for (int row = 0; row < 3; ++row) {
                table.selectRow(row);
                QCoreApplication::processEvents();
                NSArray* selected = [element accessibilitySelectedChildren];
                QVERIFY(QAccessible::accessibleInterface(identity));
                QCOMPARE(selected.count, NSUInteger(2));
            }
            table.setColumnCount(3);
            table.setItem(0, 2, new QTableWidgetItem("third column"));
            table.selectRow(0);
            QCoreApplication::processEvents();
            NSArray* selected = [element accessibilitySelectedChildren];
            QVERIFY(QAccessible::accessibleInterface(identity));
            QCOMPARE(selected.count, NSUInteger(3));
            [element release];
        }
    }
};

QTEST_MAIN(QtCocoaAccessibilityTest)
#include "QtCocoaAccessibility_test.moc"
