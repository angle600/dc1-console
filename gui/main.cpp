#include "clirunner.h"

#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickStyle>

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
    engine.load(QUrl(QStringLiteral("qrc:/qml/Main.qml")));
    if (engine.rootObjects().isEmpty())
        return -1;
    return app.exec();
}
