#include "clirunner.h"

#include <QGuiApplication>
#include <QImage>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickStyle>
#include <QQuickWindow>
#include <QTimer>

int main(int argc, char *argv[])
{
    QGuiApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("DC1 Console"));
    app.setOrganizationName(QStringLiteral("dc1-lan"));
    app.setApplicationDisplayName(QStringLiteral("DC1 Console"));

    // 统一外观（Controls 2 自带 Fusion）
    QQuickStyle::setStyle(QStringLiteral("Fusion"));

    Cli cli;

    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty(QStringLiteral("Cli"), &cli);
    // DC1_DEMO=1：注入假设备与状态，用于离线预览/截图（不连真实设备）
    engine.rootContext()->setContextProperty(QStringLiteral("DemoMode"),
                                             qEnvironmentVariableIsSet("DC1_DEMO"));
    engine.load(QUrl(QStringLiteral("qrc:/qml/Main.qml")));
    if (engine.rootObjects().isEmpty())
        return -1;

    // 无人值守截图（CI/文档用）：DC1_GRAB=out.png  +  QT_QPA_PLATFORM=offscreen
    if (!qEnvironmentVariableIsEmpty("DC1_GRAB")) {
        QTimer::singleShot(1800, &app, [&engine]() {
            if (!engine.rootObjects().isEmpty()) {
                if (auto *w = qobject_cast<QQuickWindow *>(engine.rootObjects().first())) {
                    const bool ok = w->grabWindow().save(qEnvironmentVariable("DC1_GRAB"));
                    qInfo("grab %s: %s", qPrintable(qEnvironmentVariable("DC1_GRAB")),
                          ok ? "ok" : "failed");
                }
            }
            QCoreApplication::quit();
        });
    }
    return app.exec();
}
