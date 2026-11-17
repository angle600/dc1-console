#pragma once

#include <QByteArray>
#include <QObject>
#include <QProcess>
#include <QString>
#include <QStringList>

// Cli —— GUI 与 Python CLI 的唯一通道。
// GUI 不做任何业务逻辑：所有设备操作都拼成 cli/dc1ctl.py 的命令行参数，
// 结果统一是 JSON（--json），解析后交给 QML 渲染。
class Cli : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool busy READ busy NOTIFY busyChanged)
    Q_PROPERTY(QString pythonPath READ pythonPath WRITE setPythonPath NOTIFY pythonPathChanged)
    Q_PROPERTY(QString cliPath READ cliPath CONSTANT)

public:
    explicit Cli(QObject *parent = nullptr);

    bool busy() const { return m_proc != nullptr; }
    QString pythonPath() const { return m_python; }
    void setPythonPath(const QString &p);

    QString cliPath() const;

    Q_INVOKABLE void run(const QStringList &args);

signals:
    void busyChanged();
    void pythonPathChanged();
    void finished(bool ok, const QString &json);
    void output(const QString &line);

private:
    static QString resolveCli();

    QString m_python;
    QProcess *m_proc = nullptr;
    QByteArray m_out;
};
